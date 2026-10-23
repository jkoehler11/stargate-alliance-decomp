# Stargate SG-1: The Alliance — decompilation project
#
# The game dump is never part of this repo. Point SG1_DUMP at your own copy:
#   make verify-dump SG1_DUMP=/path/to/dump
SG1_DUMP ?= $(HOME)/sg1/dump
WORK     ?= $(HOME)/sg1/work   # game-derived output lives here, outside the repo
PYTHON   ?= python3
DOTNET   ?= dotnet
TOOL     := tools/Sg1Tool/bin/Release/net10.0/Sg1Tool.dll

.PHONY: help verify-dump manifest inventory decompile oracle-diff check clean

help:
	@echo "targets:"
	@echo "  verify-dump   check the dump against inventory/manifest.sha256"
	@echo "  manifest      regenerate inventory/manifest.sha256"
	@echo "  inventory     regenerate every inventory/*.csv from the dump"
	@echo "  decompile     reconstruct UnrealScript into $(WORK)/UnrealScript (never committed)"
	@echo "  oracle-diff   compare against the private 2006 source (set ORACLE_DIR)"
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

# Needs the privately held 2006 Classes tree; deliberately not part of `make inventory`.
oracle-diff:
	@test -n "$(ORACLE_DIR)" || { echo "set ORACLE_DIR=/path/to/private/Classes (never committed)"; exit 2; }
	@$(PYTHON) tools/oracle-diff.py --oracle "$(ORACLE_DIR)" --src "$(WORK)/UnrealScript" -o inventory

check: verify-dump
	@echo "dump ok; run 'make inventory' and commit if anything drifted"

clean:
	rm -rf tools/Sg1Tool/bin tools/Sg1Tool/obj build/out
