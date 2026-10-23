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

.PHONY: help verify-dump manifest inventory natives call-census formats decompile oracle-diff ghidra check clean

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

# Native track: needs Ghidra (GHIDRA_HOME). Writes build/ghidra/ and symbols/xbox_functions.csv.
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

check: verify-dump
	@echo "dump ok; run 'make inventory' and commit if anything drifted"

clean:
	rm -rf tools/Sg1Tool/bin tools/Sg1Tool/obj build/out
