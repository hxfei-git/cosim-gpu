HIPCC    ?= hipcc
HIPFLAGS ?= --offload-arch=gfx942 -O2
BUILDDIR ?= build
TOOLBUILDDIR ?= tools-build

SOURCES := $(wildcard kernels/*.cpp)
KERNEL_TARGETS := $(patsubst kernels/%.cpp,$(BUILDDIR)/%,$(SOURCES))
TOOL_TARGETS := $(TOOLBUILDDIR)/cosim_evidence_boundary
TARGETS := $(KERNEL_TARGETS) $(TOOL_TARGETS)

.PHONY: all clean test

all: $(TARGETS)

$(BUILDDIR)/%: kernels/%.cpp common/test_utils.h | $(BUILDDIR)
	$(HIPCC) $(HIPFLAGS) -I. -o $@ $<

$(TOOLBUILDDIR)/cosim_evidence_boundary: \
    tools/cosim_evidence_boundary.cpp | $(TOOLBUILDDIR)
	$(HIPCC) -O2 -std=c++17 -o $@ $< -lhsa-runtime64

$(BUILDDIR):
	mkdir -p $(BUILDDIR)

$(TOOLBUILDDIR):
	mkdir -p $(TOOLBUILDDIR)

test: all
	@./run_tests.sh

clean:
	rm -rf $(BUILDDIR) $(TOOLBUILDDIR)
