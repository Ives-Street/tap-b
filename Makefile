PROJECT = tap

SRCDIR = src
OBJDIR = obj
BINDIR = bin
DEPDIR = .depend
INCLUDEDIR = include

$(shell mkdir -p $(DEPDIR) > /dev/null)
$(shell mkdir -p $(OBJDIR))
$(shell mkdir -p $(BINDIR))

INCLUDEFLAG = -I $(INCLUDEDIR)
POSTCOMPILE = @mv -f $(DEPDIR)/$*.Td $(DEPDIR)/$*.d && touch $@

SOURCES := $(wildcard $(SRCDIR)/*.c)
INCLUDES := $(wildcard $(INCLUDEDIR)/*.h)
OBJECTS := $(SOURCES:$(SRCDIR)/%.c=$(OBJDIR)/%.o)
RM = rm -f

CC = gcc
# IVES FORK: gcc 15 defaults to C23, where `bool` is a keyword and utils.h's own
# typedef becomes a hard error in every translation unit.  Pin the dialect the
# source was written for.
CFLAGS = -std=gnu17 -pthread -Wall $(INCLUDEFLAG) $(DEPFLAGS)
CFLAGS += -Wextra -Wwrite-strings -Wno-parentheses -Winline
CFLAGS += -Wpedantic -Warray-bounds
CFLAGS += -DPARALLELISM=1 # Parallel is now default; use make serial if unwanted
CFLAGS += -O2 # After testing, O2 is fastest; now make this default.
DEBUGFLAGS = -g -O0
RELEASEFLAGS = # Currently no extra flags for release option
PROFILEFLAGS = -pg $(DEBUGFLAGS)
LINKER = gcc
LFLAGS = -Wall -pthread -lm $(INCLUDEFLAG)
MACFLAGS = -Wall -lm $(INCLUDEFLAG)
DEPFLAGS = -MT $@ -MMD -MP -MF $(DEPDIR)/$*.Td


# ------- all target: build the main project ------

.PHONY: all
all: CFLAGS += $(DEFAULTFLAGS)
all: $(BINDIR)/$(PROJECT)

$(BINDIR)/$(PROJECT): $(OBJECTS)
	$(LINKER) $^ $(LFLAGS) -o $@

# ------- serial target: build the main project ------
.PHONY: serial
serial: CFLAGS += -UPARALLELISM 
serial: $(BINDIR)/$(PROJECT)

# ------- serial debug: build the main project ------
.PHONY: serial-d
serial-d: CFLAGS += -UPARALLELISM -g
serial-d: $(BINDIR)/$(PROJECT)

# ---------- release target: extra optimization ----

.PHONY: release
release: CFLAGS += $(RELEASEFLAGS)
release: $(BINDIR)/$(PROJECT)

# ---------- debug target---------------------------

.PHONY: debug
debug: CFLAGS += $(DEBUGFLAGS)
debug: $(BINDIR)/$(PROJECT)

# ---------- profile target-------------------------

.PHONY: debug
profile: CFLAGS += $(PROFILEFLAGS)
profile: $(BINDIR)/$(PROJECT)

# ---------- test target (IVES FORK) ----------------
# Direct checks on the preloaded link performance functions; see
# test/bpr_preload_test.c for why these cannot be proven by a solve.

.PHONY: test
test: $(BINDIR)/bpr_preload_test
	$(BINDIR)/bpr_preload_test

$(BINDIR)/bpr_preload_test: test/bpr_preload_test.c $(OBJDIR)/tap.o \
                            $(OBJDIR)/networks.o $(OBJDIR)/datastructures.o \
                            $(OBJDIR)/utils.o $(OBJDIR)/bush.o \
                            $(OBJDIR)/parallel_bush.o $(OBJDIR)/fileio.o \
                            $(OBJDIR)/convexcombination.o $(OBJDIR)/thpool.o
	$(CC) -std=gnu17 -pthread -Wall $(INCLUDEFLAG) -DPARALLELISM=1 -O2 \
	    $^ -lm -o $@

# ---------- compile objects

$(OBJDIR)/%.o: $(SRCDIR)/%.c $(DEPDIR)/%.d
	$(CC) $(CFLAGS) -c $< $(INCLUDEFLAG) -o $@
	$(POSTCOMPILE)

$(OBJDIR)/%.o: $(TESTDIR)/%.c
	$(CC) $(CFLAGS_TEST) -c $< $(INCLUDEFLAG_TEST) -o $@
	$(POSTCOMPILE)

# ---------- clean/clear
.PHONY: clean clear
clean clear:
	@$(RM) -r .depend
	@$(RM) $(OBJECTS)

.PHONY: remove
remove: clean
	@$(RM) $(BINDIR)/$(PROJECT)

.DELETE_ON_ERROR:

# -------- dependency tracking
$(DEPDIR)/%.d: ;
.PRECIOUS: $(DEPDIR)/%.d

# IVES FORK: this was $(SRCS), a variable that is never defined -- so the
# generated .d files were written and then never included, and an incremental
# build ignored header changes entirely.  Editing networks.h (which is where
# arc_type lives) recompiled only the .c files touched in the same edit and
# linked them against objects still using the *old* struct layout: a binary that
# reads every arc field at the wrong offset and segfaults somewhere unrelated.
# The fix is one word; the symptom was expensive.
include $(wildcard $(patsubst %,$(DEPDIR)/%.d,$(basename $(notdir $(SOURCES)))))


