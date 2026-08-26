#include <hsa/hsa.h>

#include <algorithm>
#include <array>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <iterator>
#include <limits>
#include <stdexcept>
#include <string>
#include <thread>

namespace
{

constexpr size_t BoundaryMagicSize = 8;
constexpr size_t BoundaryTokenSize = 16;
constexpr uint16_t BoundaryVersion = 1;
constexpr uint16_t BoundaryCanonicalHeader = 0x1400;
constexpr uint8_t BoundaryMagic[BoundaryMagicSize] = {
    'C', 'O', 'S', 'I', 'M', 'A', 'Q', 'L'};
constexpr auto BoundaryWaitLimit = std::chrono::seconds(60);
constexpr uint16_t BoundaryHsaHeader =
    (HSA_PACKET_TYPE_VENDOR_SPECIFIC << HSA_PACKET_HEADER_TYPE) |
    (HSA_FENCE_SCOPE_SYSTEM <<
     HSA_PACKET_HEADER_SCACQUIRE_FENCE_SCOPE) |
    (HSA_FENCE_SCOPE_SYSTEM <<
     HSA_PACKET_HEADER_SCRELEASE_FENCE_SCOPE);

static_assert(BoundaryHsaHeader == BoundaryCanonicalHeader);

enum class BoundaryPhase : uint8_t
{
    Begin = 1,
    End = 2,
};

struct BoundaryPacket
{
    uint16_t header;
    uint8_t magic[BoundaryMagicSize];
    uint16_t version;
    uint8_t phase;
    uint8_t reserved0[3];
    uint8_t token[BoundaryTokenSize];
    uint8_t reserved1[24];
    uint64_t completionSignal;
};

static_assert(sizeof(BoundaryPacket) == 64);
static_assert(offsetof(BoundaryPacket, magic) == 2);
static_assert(offsetof(BoundaryPacket, version) == 10);
static_assert(offsetof(BoundaryPacket, phase) == 12);
static_assert(offsetof(BoundaryPacket, token) == 16);
static_assert(offsetof(BoundaryPacket, completionSignal) == 56);

std::string
statusDescription(hsa_status_t status)
{
    const char *description = nullptr;
    if (hsa_status_string(status, &description) == HSA_STATUS_SUCCESS &&
        description != nullptr) {
        return description;
    }
    return "HSA 状态码 " + std::to_string(static_cast<unsigned>(status));
}

void
checkStatus(hsa_status_t status, const char *operation)
{
    if (status != HSA_STATUS_SUCCESS) {
        throw std::runtime_error(
            std::string(operation) + "失败：" + statusDescription(status));
    }
}

void
appendCleanupError(std::string &errors, const char *operation,
                   hsa_status_t status)
{
    if (status == HSA_STATUS_SUCCESS) {
        return;
    }
    if (!errors.empty()) {
        errors += "；";
    }
    errors += std::string(operation) + "失败：" + statusDescription(status);
}

struct RuntimeResources
{
    explicit RuntimeResources(bool &cleanup_failed)
        : cleanupFailed(cleanup_failed)
    {
    }

    RuntimeResources(const RuntimeResources &) = delete;
    RuntimeResources &operator=(const RuntimeResources &) = delete;

    bool initialized = false;
    hsa_queue_t *queue = nullptr;
    hsa_signal_t signal = {};
    bool signalCreated = false;
    bool packetSubmitted = false;
    bool queueRetired = false;

    ~RuntimeResources()
    {
        const std::string errors = close();
        if (!errors.empty()) {
            cleanupFailed = true;
            std::cerr << "[FAIL] HSA 资源清理失败：" << errors << '\n';
        }
    }

    std::string
    close()
    {
        std::string errors;
        if (packetSubmitted && !queueRetired) {
            queue = nullptr;
            signal = {};
            signalCreated = false;
            initialized = false;
            return "AQL packet/read index 尚未退休，拒绝销毁 HSA 资源";
        }
        if (queue != nullptr) {
            hsa_queue_t *queue_to_destroy = queue;
            queue = nullptr;
            appendCleanupError(errors, "销毁 HSA 队列",
                               hsa_queue_destroy(queue_to_destroy));
        }
        if (signalCreated) {
            const hsa_signal_t signal_to_destroy = signal;
            signal = {};
            signalCreated = false;
            appendCleanupError(errors, "销毁 HSA 完成信号",
                               hsa_signal_destroy(signal_to_destroy));
        }
        if (initialized) {
            initialized = false;
            appendCleanupError(errors, "关闭 HSA runtime", hsa_shut_down());
        }
        return errors;
    }

    void
    markPacketSubmitted()
    {
        packetSubmitted = true;
    }

    void
    markQueueRetired()
    {
        queueRetired = true;
    }

  private:
    bool &cleanupFailed;
};

struct AgentSelection
{
    hsa_agent_t agent = {};
    bool found = false;
};

hsa_status_t
selectFirstGpu(hsa_agent_t agent, void *data)
{
    auto *selection = static_cast<AgentSelection *>(data);
    hsa_device_type_t device_type;
    const hsa_status_t status =
        hsa_agent_get_info(agent, HSA_AGENT_INFO_DEVICE, &device_type);
    if (status != HSA_STATUS_SUCCESS) {
        return status;
    }
    if (device_type == HSA_DEVICE_TYPE_GPU) {
        selection->agent = agent;
        selection->found = true;
        return HSA_STATUS_INFO_BREAK;
    }
    return HSA_STATUS_SUCCESS;
}

bool
lowerHex(char value)
{
    return (value >= '0' && value <= '9') ||
           (value >= 'a' && value <= 'f');
}

uint8_t
hexValue(char value)
{
    return static_cast<uint8_t>(
        value <= '9' ? value - '0' : value - 'a' + 10);
}

std::array<uint8_t, BoundaryTokenSize>
parseToken(const std::string &text)
{
    if (text.size() != BoundaryTokenSize * 2 ||
        !std::all_of(text.begin(), text.end(), lowerHex)) {
        throw std::runtime_error("token 必须是 32 位小写十六进制字符串");
    }

    std::array<uint8_t, BoundaryTokenSize> token = {};
    for (size_t index = 0; index < token.size(); ++index) {
        token[index] = (hexValue(text[index * 2]) << 4) |
                       hexValue(text[index * 2 + 1]);
    }
    return token;
}

BoundaryPhase
parsePhase(const std::string &text)
{
    if (text == "begin") {
        return BoundaryPhase::Begin;
    }
    if (text == "end") {
        return BoundaryPhase::End;
    }
    throw std::runtime_error("phase 必须是 begin 或 end");
}

uint64_t
publishPacket(hsa_queue_t *queue, const BoundaryPacket &packet)
{
    const uint64_t write_index =
        hsa_queue_add_write_index_relaxed(queue, 1);
    while (write_index - hsa_queue_load_read_index_scacquire(queue) >=
           queue->size) {
        std::this_thread::yield();
    }

    auto *slot = static_cast<uint8_t *>(queue->base_address) +
                 (write_index & (queue->size - 1)) * sizeof(BoundaryPacket);
    std::memcpy(slot + sizeof(packet.header),
                reinterpret_cast<const uint8_t *>(&packet) +
                    sizeof(packet.header),
                sizeof(packet) - sizeof(packet.header));
    __atomic_store_n(reinterpret_cast<uint16_t *>(slot), packet.header,
                     __ATOMIC_RELEASE);
    hsa_signal_store_screlease(
        queue->doorbell_signal,
        static_cast<hsa_signal_value_t>(write_index));
    return write_index;
}

bool
waitForCompletion(hsa_signal_t signal)
{
    uint64_t timestamp_frequency = 0;
    checkStatus(hsa_system_get_info(HSA_SYSTEM_INFO_TIMESTAMP_FREQUENCY,
                                    &timestamp_frequency),
                "读取 HSA 时间戳频率");
    const uint64_t one_second = std::max<uint64_t>(timestamp_frequency, 1);
    const auto deadline = std::chrono::steady_clock::now() + BoundaryWaitLimit;

    while (std::chrono::steady_clock::now() < deadline) {
        const hsa_signal_value_t observed = hsa_signal_wait_scacquire(
            signal, HSA_SIGNAL_CONDITION_EQ, 0, one_second,
            HSA_WAIT_STATE_ACTIVE);
        if (observed == 0) {
            return true;
        }
    }
    return hsa_signal_load_scacquire(signal) == 0;
}

bool
waitForQueueRetirement(const hsa_queue_t *queue, uint64_t submitted_index)
{
    const auto deadline = std::chrono::steady_clock::now() + BoundaryWaitLimit;
    while (std::chrono::steady_clock::now() < deadline) {
        if (hsa_queue_load_read_index_scacquire(queue) > submitted_index) {
            return true;
        }
        std::this_thread::yield();
    }
    return hsa_queue_load_read_index_scacquire(queue) > submitted_index;
}

} // anonymous namespace

int
main(int argc, char **argv)
{
    if (argc != 3) {
        std::cerr << "用法：" << argv[0] << " begin|end <32位小写十六进制token>\n";
        return 2;
    }

    int exit_code = 1;
    bool cleanup_failed = false;
    {
        RuntimeResources resources(cleanup_failed);
        try {
            const BoundaryPhase phase = parsePhase(argv[1]);
            const auto token = parseToken(argv[2]);

            checkStatus(hsa_init(), "初始化 HSA runtime");
            resources.initialized = true;

            AgentSelection selection;
            const hsa_status_t iterate_status =
                hsa_iterate_agents(selectFirstGpu, &selection);
            if (iterate_status != HSA_STATUS_SUCCESS &&
                iterate_status != HSA_STATUS_INFO_BREAK) {
                checkStatus(iterate_status, "枚举 HSA agent");
            }
            if (!selection.found) {
                throw std::runtime_error("未找到 GPU HSA agent");
            }

            uint32_t queue_size = 0;
            checkStatus(hsa_agent_get_info(selection.agent,
                                           HSA_AGENT_INFO_QUEUE_MIN_SIZE,
                                           &queue_size),
                        "读取 GPU 最小队列大小");
            if (queue_size == 0 || (queue_size & (queue_size - 1)) != 0) {
                throw std::runtime_error("GPU 返回了无效的最小队列大小");
            }
            hsa_queue_t *created_queue = nullptr;
            checkStatus(
                hsa_queue_create(selection.agent, queue_size,
                                 HSA_QUEUE_TYPE_SINGLE, nullptr, nullptr,
                                 std::numeric_limits<uint32_t>::max(),
                                 std::numeric_limits<uint32_t>::max(),
                                 &created_queue),
                "创建 HSA 队列");
            resources.queue = created_queue;

            hsa_signal_t created_signal = {};
            checkStatus(hsa_signal_create(1, 0, nullptr, &created_signal),
                        "创建完成信号");
            resources.signal = created_signal;
            resources.signalCreated = true;

            BoundaryPacket packet = {};
            packet.header = BoundaryCanonicalHeader;
            std::copy(std::begin(BoundaryMagic), std::end(BoundaryMagic),
                      packet.magic);
            packet.version = BoundaryVersion;
            packet.phase = static_cast<uint8_t>(phase);
            std::copy(token.begin(), token.end(), packet.token);
            packet.completionSignal = resources.signal.handle;

            const uint64_t submitted_index =
                publishPacket(resources.queue, packet);
            resources.markPacketSubmitted();
            if (!waitForCompletion(resources.signal)) {
                throw std::runtime_error("等待 gem5 持久化边界记录超时");
            }
            if (!waitForQueueRetirement(resources.queue, submitted_index)) {
                throw std::runtime_error("等待 AQL packet/read index 退休超时");
            }
            resources.markQueueRetired();

            const std::string cleanup_errors = resources.close();
            if (!cleanup_errors.empty()) {
                throw std::runtime_error(cleanup_errors);
            }

            std::cout << "[PASS] gem5 已确认 " << argv[1] << " 边界\n";
            exit_code = 0;
        } catch (const std::exception &error) {
            std::cerr << "[FAIL] cosim evidence 边界失败：" << error.what()
                      << '\n';
        }
    }
    return cleanup_failed ? 1 : exit_code;
}
