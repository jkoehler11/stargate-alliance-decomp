# Stargate SG-1: The Alliance — decompilation project
#
# The game dump is never part of this repo. Point SG1_DUMP at your own copy:
#   make verify-dump SG1_DUMP=/path/to/dump
# game-derived output lives here, outside the repo
SG1_DUMP ?= $(HOME)/sg1/dump
WORK     ?= $(HOME)/sg1/work
GHIDRA_HOME ?= $(HOME)/tools/ghidra/ghidra_12.2_PUBLIC
PYTHON   ?= python3
DOTNET   ?= dotnet
TOOL     := tools/Sg1Tool/bin/Release/net10.0/Sg1Tool.dll

.PHONY: help verify-dump manifest inventory natives call-census formats decompile oracle-diff ghidra class-tables class-names symbols libraries assert-residual status checkpoints fork-surface ucpp-vtables ci check clean

help:
	@echo "targets:"
	@echo "  verify-dump   check the dump against inventory/manifest.sha256"
	@echo "  manifest      regenerate inventory/manifest.sha256"
	@echo "  inventory     regenerate every inventory/*.csv from the dump"
	@echo "  decompile     reconstruct UnrealScript into $(WORK)/UnrealScript (never committed)"
	@echo "  oracle-diff   compare against the private 2006 source (set ORACLE_DIR)"
	@echo "  formats       catalogue custom formats; dump the checkpoint property schema"
	@echo "  call-census   direct callers of one function from the Ghidra export (needs ghidra)"
	@echo "  ghidra        import default.xbe, carve sections, auto-analyse, name from asserts"
	@echo "  class-tables  find the per-class code-pointer tables and ProcessEvent's slot"
	@echo "  class-names   name those tables from the build's class registrations (needs capstone)"
	@echo "  symbols       emit symbols/xbox.map and the assembler form a debugger loads"
	@echo "  libraries     list the libraries the XBE was linked against (PLAN 2.6)"
	@echo "  status        regenerate the progress table in README.md and docs/STATUS.md"
	@echo "  checkpoints   roll the .sgs schema up per class (PLAN 8.3, checkpoint system)"
	@echo "  fork-surface  list every class the fork inserts onto a stock UE2.5 base (PLAN 4.8)"
	@echo "  ucpp-vtables  resolve the class tables of the six ucpp-native classes (PLAN 4.7)"
	@echo "  assert-residual  classify the assert sites that are still in no function (needs asserts.tsv)"
	@echo "  ci            the checks that need no game data (committed CSVs, symbol map, generated docs)"
	@echo "  check         verify-dump + inventory drift check"

verify-dump:
	@test -d "$(SG1_DUMP)" || { echo "SG1_DUMP not found: $(SG1_DUMP)"; exit 2; }
	@$(PYTHON) tools/make-manifest.py "$(SG1_DUMP)" \
		-o inventory/manifest.sha256 --check

manifest:
	@test -d "$(SG1_DUMP)" || { echo "SG1_DUMP not found: $(SG1_DUMP)"; exit 2; }
	@$(PYTHON) tools/make-manifest.py "$(SG1_DUMP)" -o inventory/manifest.sha256

inventory:
	@test -d "$(SG1_DUMP)" || { echo "SG1_DUMP not found: $(SG1_DUMP)"; exit 2; }
	@$(PYTHON) tools/file-stats.py "$(SG1_DUMP)" -o inventory
	@$(DOTNET) run --project tools/Sg1Tool -c Release -- \
		inventory --dump "$(SG1_DUMP)" --out inventory
	@$(PYTHON) tools/xbe.py "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" \
		asserts -o inventory
	@$(PYTHON) tools/gen-inventory-doc.py

decompile:
	@test -d "$(SG1_DUMP)" || { echo "SG1_DUMP not found: $(SG1_DUMP)"; exit 2; }
	@$(DOTNET) run --project tools/Sg1Tool -c Release -- \
		decompile --dump "$(SG1_DUMP)" --out "$(WORK)/UnrealScript"
	@$(PYTHON) tools/token-gaps.py "$(WORK)/UnrealScript"

# Native track: needs Ghidra (GHIDRA_HOME) and a JDK 25 on JAVA_HOME (or under $HOME/tools/jdk-*).
# Writes build/ghidra/, symbols/xbox_functions.csv and inventory/xbe_block_seeds.csv. The analysis also
# seeds functions for code that nothing calls directly (tools/ghidra/ghidra_scripts/SeedGaps.java),
# which is the only way the VM's own routines get analysed at all (docs/NATIVE_VM.md). Block seeds are
# promoted blocks rather than function entries and are listed there so nothing mistakes them for one.
# Run `make ghidra`, then `make natives`, then `make ghidra` again: the dispatch table can only be
# located against a finished analysis, and naming its targets is a post-script of the next import.
ghidra:
	@test -d "$(SG1_DUMP)" || { echo "SG1_DUMP not found: $(SG1_DUMP)"; exit 2; }
	@GHIDRA_HOME="$(GHIDRA_HOME)" sh tools/ghidra/run-analysis.sh "$(SG1_DUMP)" "$(GHIDRA_HOME)"

# GNatives: locate the dispatch table from the VM's own assert text, and name the indices the
# packages declare. There is no index -> address map: the table has no bytes in the file.
# The runtime dump is taken from the emulator (docs/XEMU.md) and never committed: it is guest memory.
# What gets committed is inventory/gnatives_slots.csv, the derived index -> address map.
natives:
	@test -d "$(SG1_DUMP)" || { echo "SG1_DUMP not found: $(SG1_DUMP)"; exit 2; }
	@test -f "$(WORK)/gnatives/runtime.bin" || { \
		echo "no runtime dump at $(WORK)/gnatives/runtime.bin -- see docs/XEMU.md"; exit 2; }
	@$(PYTHON) tools/gnatives.py \
		"$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" -o inventory \
		--runtime "$(WORK)/gnatives/runtime.bin"
	@$(PYTHON) tools/native-names.py --natives inventory/natives.csv -o inventory \
		--slots inventory/gnatives_slots.csv --symbols-out symbols/xbe_dispatch.csv

# Who calls a function: the call graph from `make ghidra`, keyed by address or final name.
call-census:
	@test -d build/ghidra/evidence || { echo "build/ghidra/evidence missing -- run 'make ghidra'"; exit 2; }
	@$(PYTHON) tools/call-census.py --target "$(TARGET)"

# Custom formats: counts from the manifest, containers from the dump, checkpoint schemas via Sg1Tool.
formats:
	@test -d "$(SG1_DUMP)" || { echo "SG1_DUMP not found: $(SG1_DUMP)"; exit 2; }
	@$(PYTHON) tools/formats.py --manifest inventory/manifest.sha256 --samples "$(SG1_DUMP)" -o inventory
	@sh tools/sgs-dump.sh "$(SG1_DUMP)" "$(WORK)/sgs"
	@$(PYTHON) tools/sgs-schema.py "$(WORK)/sgs" -o inventory/sgs_schema.csv

# Needs the privately held 2006 Classes tree; deliberately not part of `make inventory`.
oracle-diff:
	@test -n "$(ORACLE_DIR)" || { echo "set ORACLE_DIR=/path/to/private/Classes (never committed)"; exit 2; }
	@$(PYTHON) tools/oracle-diff.py --oracle "$(ORACLE_DIR)" --src "$(WORK)/UnrealScript" -o inventory

# Everything CI can check without the dump: CSV shapes, the function and symbol maps, and that the
# generated docs reproduce. AGENTS.md forbids CI from downloading the game, so this is the whole of the
# per-commit gate; the dump-side checks stay in `make check` and `make ghidra`.
ci:
	@$(PYTHON) tools/ci.py
	@$(PYTHON) tools/status.py --check

check: verify-dump
	@echo "dump ok; run 'make inventory' and commit if anything drifted"

clean:
	rm -rf tools/Sg1Tool/bin tools/Sg1Tool/obj build/out

# Where ProcessEvent is dispatched from: the per-class tables of code pointers in .rdata and the
# slot that holds it (docs/NATIVE_VM.md, docs/UNKNOWNS.md U8). Reads the image only.
class-tables:
	@test -d "$(SG1_DUMP)" || { echo "SG1_DUMP not found: $(SG1_DUMP)"; exit 2; }
	@$(PYTHON) tools/class-tables.py \
		"$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" -o inventory

# Which class owns each table, and every class's object size, from the class registrations
# (docs/NATIVE_CLASSES.md). Needs capstone: pip install capstone.
class-names:
	@test -d "$(SG1_DUMP)" || { echo "SG1_DUMP not found: $(SG1_DUMP)"; exit 2; }
	@$(PYTHON) tools/class-table-names.py \
		"$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" -o inventory

# Which assert sites are still in no function, and whether the bytes there are code or a dword in a
# data table (docs/UNKNOWNS.md U8). Reads the image and the committed map, writes
# inventory/xbe_assert_residual.csv. Needs the assert scan from a `make ghidra` run.
ASSERT_TSV ?= build/ghidra/asserts.tsv
assert-residual:
	@test -d "$(SG1_DUMP)" || { echo "SG1_DUMP not found: $(SG1_DUMP)"; exit 2; }
	@test -f "$(ASSERT_TSV)" || { echo "no $(ASSERT_TSV) -- run 'make ghidra' first"; exit 2; }
	@$(PYTHON) tools/assert-residual.py \
		"$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" -o inventory \
		--functions symbols/xbox_functions.csv --asserts "$(ASSERT_TSV)"

# The published symbol map (PLAN 4.10, docs/SYMBOLS.md). xbox.map is committed; the assembler form is
# regenerated and stays out of git.
symbols:
	@$(PYTHON) tools/make-symbols.py $(if $(SG1_DUMP),--xbe "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe") -o symbols
	@as --32 -o symbols/xbox.sym.o symbols/xbox.sym.s
	@echo "load it: gdb -batch -ex \"symbol-file symbols/xbox.sym.o\""

# The libraries the image was linked against (PLAN 2.6, docs/IMPORTS.md). Reads the header only.
libraries:
	@test -f "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" || \
		{ echo "no default.xbe under $(SG1_DUMP)"; exit 2; }
	@$(PYTHON) tools/xbe-libraries.py \
		"$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" -o inventory

# The progress table (docs/STATUS.md and the README block). Reads committed artifacts only; `make ci`
# runs it with --check, so a landed artifact that moves a number fails the gate until it is regenerated.
status:
	@$(PYTHON) tools/status.py

# Per-class picture of what a checkpoint persists (docs/systems/CHECKPOINTS.md). Reads the committed
# schema only, so it needs no dump.
checkpoints:
	@$(PYTHON) tools/checkpoint-report.py -o inventory

# The fork's script surface: fork classes whose super is a stock engine class (docs/systems/FORK_SURFACE.md).
fork-surface:
	@$(PYTHON) tools/fork-surface.py -o inventory

# Vtable slots of the six classes that declare ucpp natives (docs/systems/UCPP_NATIVES.md). Needs the dump.
ucpp-vtables:
	@$(PYTHON) tools/ucpp-vtables.py "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" -o inventory
