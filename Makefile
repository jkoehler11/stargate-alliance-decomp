# Stargate SG-1: The Alliance — decompilation project
#
# The game dump is never part of this repo. Point SG1_DUMP at your own copy:
#   make verify-dump SG1_DUMP=/path/to/dump
# game-derived output lives here, outside the repo
SG1_DUMP ?= $(HOME)/sg1/dump
WORK     ?= $(HOME)/sg1/work
GHIDRA_HOME ?= $(HOME)/tools/ghidra/ghidra_12.2_PUBLIC
PYTHON   ?= python3
RESIZE_BYTES ?= 64
# the privately held reference decompile, only the token map needs it
ORACLE_DIR ?=
DOTNET   ?= dotnet
TOOL     := tools/Sg1Tool/bin/Release/net10.0/Sg1Tool.dll

.PHONY: campaign-matrix content-census dialogue-index property-types token-map game-modules package-check package-check-code savegame-paths maps-parse artifact-hashes versions class-reach rebuild serialize-slots opcode-table opcode-table-check function-flags lipsinc-index lipsinc-runtime native-reconcile defaults-emit defaults-tree defaults-coverage defaults-per-class help pkg-verify pkg-rebuild pkg-layout pkg-resize verify-dump manifest inventory natives call-census formats decompile oracle-diff ghidra class-tables class-names symbols libraries assert-residual status checkpoints fork-surface ucpp-vtables release-notes class-graph package-names nightly ci check clean

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
	@echo "  squad-rosters  read every SQUAD_* roster class (needs the dump)"
	@echo "  pkg-verify    re-encode every package's tables and diff against the original"
	@echo "  pkg-rebuild   rebuild whole packages from header + tables + payloads and diff"
	@echo "  pkg-layout    census of where each region sits in a package"
	@echo "  pkg-resize    re-lay every package with one payload resized and round-trip it"
	@echo "  compile-readiness  per-class check of what a rebuild would have (3.6 inputs)"
	@echo "  map-census    regenerate the map-side census CSVs (needs the dump)"
	@echo "  package-census  regenerate the four package-side census CSVs (needs the dump)"
	@echo "  inventory-index  index every committed artifact and what regenerates it"
	@echo "  status        regenerate the progress table in README.md and docs/STATUS.md"
	@echo "  checkpoints   roll the .sgs schema up per class (PLAN 8.3, checkpoint system)"
	@echo "  fork-surface  list every class the fork inserts onto a stock UE2.5 base (PLAN 4.8)"
	@echo "  ucpp-vtables  resolve the class tables of the six ucpp-native classes (PLAN 4.7)"
	@echo "  release-notes generate the body for a milestone release (MILESTONE=a..f)"
	@echo "  class-graph   walk the subclass tree under a root (ROOT=BotBase) with states and usage"
	@echo "  package-names parse every .u header and name table (PLAN 5.2/4.7 prerequisite)"
	@echo "  nightly       run the dump-free gate plus every dump-side check, one summary per run (PLAN 7.5)"
	@echo "  pkg-exports   list a class's exports and their sizes from a package (PKG=... CLASS=...)"
	@echo "  extract-script write every class's shipped source into $WORK (never the repo)"
	@echo "  map-actors    histogram every .unr's export classes (PLAN 5.2, class-level)"
	@echo "  sfx-index     count the Sound exports in every .uax (PLAN 5.3)"
	@echo "  prop-stream   read one map export's property block (MAP=... EXPORT=...)"
	@echo "  t3d           write every map's actors as T3D into \$$WORK/extracted/maps (PLAN 5.2)"
	@echo "  class-defaults read one class's default properties (PKG=... CLASS=...)"
	@echo "  defaults-emit  render every class's defaultproperties block (PLAN 3.5, needs the dump)"
	@echo "  defaults-coverage  roll the defaults locator up per package (needs the dump)"
	@echo "  defaults-per-class per-class defaults rows: block size, declared fraction (needs the dump)"
	@echo "  class-reach   every class vs the content that instantiates it (PLAN 5.6, no dump needed)"
	@echo "  native-reconcile  script native() declarations vs the native records (PLAN 3.9, needs the dump)"
	@echo "  rebuild       run the rebuild pipeline stage by stage (PLAN 6.1; needs the dump)"
	@echo "  serialize-slots  every class's Serialize override from its class table (PLAN 3.7; needs the dump)"
	@echo "  opcode-table  the walker's opcode map and per-group operand rules (PLAN 3.7; needs the dump)"
	@echo "  opcode-table-check  validate the committed table with no image (what make ci runs)"
	@echo "  function-flags  every function's FunctionFlags from its export payload (docs/systems/NETWORK.md; needs the dump)"
	@echo "  lipsinc-index  the LIPSinc .map phonemes and .lad containers (docs/formats/lipsinc-lad.md; needs the dump)"
	@echo "  lipsinc-runtime  the LIPSinc support functions, from their own log strings (needs the dump)"
	@echo "  campaign-matrix  missions x maps x speech x music x chapters (dump-free: reads committed CSVs)"
	@echo "  content-census   which game classes are placed in a map (needs the dump and WORK/UnrealScript)"
	@echo "  dialogue-index   speech lines per mission, from the Speech/ WAV names (needs the dump)"
	@echo "  property-types   UnProp property-type census from ExportAt output (needs WORK/native)"
	@echo "  token-map        the bytecode token map calibrated against the private tree (needs ORACLE_DIR)"
	@echo "  game-modules     per-module function counts from a native index (needs WORK/native)"
	@echo "  package-check    the Packages.md5 check objects (needs the dump)"
	@echo "  package-check-code  the functions that read the check file, from the image (needs the dump)"
	@echo "  savegame-paths   the save/load call sites, read from the shipped ScriptText (needs the dump)"
	@echo "  maps-parse       map summary from the package headers (needs the dump and Sg1Tool)"
	@echo "  artifact-hashes  the expected SHA-256 of every committed artifact (checked in make ci)"
	@echo "  versions         check tools/versions.lock against this machine (--strict to fail on a mismatch)"
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
	@sh -n tools/build.sh && echo "ok   build-pipeline: tools/build.sh parses"
	@$(PYTHON) -m py_compile tools/xemu/mem.py tools/xemu/bootcheck.py \
		&& echo "ok   xemu-tools: mem.py and bootcheck.py compile"
	@$(PYTHON) tools/class-reach.py --check
	@$(PYTHON) tools/pkg-write.py --selftest
	@$(PYTHON) tools/inventory-index.py --check
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
squad-rosters:
	@$(PYTHON) tools/squad-rosters.py "$(SYSTEM_DIR)" -o inventory

pkg-verify:
	@$(PYTHON) tools/pkg-write.py "$(SYSTEM_DIR)"

pkg-rebuild:
	@$(PYTHON) tools/pkg-write.py "$(SYSTEM_DIR)" --rebuild

pkg-layout:
	@$(PYTHON) tools/pkg-write.py "$(SYSTEM_DIR)" --layout --write inventory
	@echo "wrote inventory/package_layout.csv"

# One payload resized by RESIZE_BYTES on five sampled exports per package, then shrunk back: the round trip is what
# proves the relayout, since a table diff cannot see a region in the wrong place. Measured artifact only.
pkg-resize:
	@$(PYTHON) tools/pkg-write.py "$(SYSTEM_DIR)" --resize $(RESIZE_BYTES) --write inventory
	@echo "wrote inventory/package_relayout.csv (the resized packages themselves stay out of the repo)"

compile-readiness:
	@$(PYTHON) tools/compile-readiness.py -o inventory

map-census:
	@$(PYTHON) tools/map-census.py "$(MAPS_DIR)" -o inventory

package-census:
	@$(PYTHON) tools/package-census.py "$(SYSTEM_DIR)" -o inventory

inventory-index:
	@$(PYTHON) tools/inventory-index.py

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

# Generated release notes for a PLAN 8.4 milestone (docs/RELEASES.md).
release-notes:
	@$(PYTHON) tools/release-notes.py --milestone "$(or $(MILESTONE),a)"

# Subclass tree under a root, with states and content usage (docs/systems/AI.md, VEHICLES.md).
class-graph:
	@$(PYTHON) tools/class-graph.py --root "$(or $(ROOT),BotBase)" -o inventory

# Header + name table of every shipped package (docs/PACKAGE_READER.md). Needs the dump.
package-names:
	@$(PYTHON) tools/ue2pkg.py "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/System"

# Nightly regression: the checks CI cannot run (docs/REGRESSION.md).
nightly:
	@sh tools/nightly.sh

# One class's exports (names, kinds, sizes) straight from a package (docs/PACKAGE_SCRIPT_TEXT.md).
pkg-exports:
	@$(PYTHON) tools/pkg-exports.py "$(PKG)" --class "$(CLASS)"

# Every class's shipped source into $WORK (docs/PACKAGE_SCRIPT_TEXT.md). Game-derived: never commit it.
extract-script:
	@$(PYTHON) tools/pkg-script-text.py "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/System" \
		--tree --write "$(or $(WORK),work)/source-tree" -o inventory

# Per-map actor-class counts from the export table (docs/MAP_EXPORTS.md). Needs the dump.
map-actors:
	@$(PYTHON) tools/map-actors.py "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/Maps" -o inventory

# Sound effects inside the .uax packages (PLAN 5.3). Needs the dump.
sfx-index:
	@$(PYTHON) tools/sfx-index.py "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/Sounds" -o inventory

# One export's property block, or a class sweep (docs/ACTOR_PROPERTIES.md).
prop-stream:
	@$(PYTHON) tools/prop-stream.py "$(MAP)" $(if $(CLASS),--class "$(CLASS)" --scan,--export $(EXPORT))

# Every map's actors as T3D, into $WORK (docs/T3D.md). Game content: never committed.
t3d:
	@$(PYTHON) tools/map-to-t3d.py "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/Maps" \
		--out "$(or $(WORK),work)/extracted/maps"

# One class's default properties (docs/CLASS_DEFAULTS.md).
class-defaults:
	@$(PYTHON) tools/class-defaults.py "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/System" --class "$(CLASS)"

# Every class's defaults, rendered back to UnrealScript (docs/CLASS_DEFAULTS.md). Needs the dump, so this is a
# nightly/local check rather than a `make ci` one; `make defaults-tree` is what the compile loop consumes.
defaults-emit:
	@$(PYTHON) tools/defaults-emit.py "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/System" --sweep -o inventory
	@echo "wrote inventory/class_defaults_emit.csv"

# The rebuild pipeline, one stage at a time (docs/REBUILD.md, PLAN 6.1). Needs the dump; the compiler stages
# report SKIP with their reason rather than pretending.
rebuild:
	@tools/build.sh

# The locator's own census, and the per-class rows behind it (docs/CLASS_DEFAULTS.md). Both artifacts existed
# without a generator; these targets are what `make inventory-index` now resolves them to.
defaults-coverage:
	@$(PYTHON) tools/class-defaults.py --coverage "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/System" -o inventory
	@echo "wrote inventory/class_defaults_coverage.csv"

defaults-per-class:
	@$(PYTHON) tools/class-defaults.py --packages "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/System" -o inventory
	@echo "wrote inventory/class_defaults_per_class.csv"

# Native declarations in the shipped script against inventory/natives.csv (docs/NATIVE_CLASSES.md, PLAN 3.9).
native-reconcile:
	@$(PYTHON) tools/native-reconcile.py "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/System" -o inventory
	@echo "wrote inventory/native_reconcile.csv"

# Which classes override Serialize, and where the code is (docs/NATIVE_VM.md, PLAN 3.7). Needs the image.
serialize-slots:
	@$(PYTHON) tools/class-serialize-slots.py --xbe "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" -o inventory
	@echo "wrote inventory/class_serialize_slots.csv"

# The script walker's opcode map and per-group operand rules (docs/BYTECODE.md, PLAN 3.7). Needs the image.
opcode-table:
	@$(PYTHON) tools/opcode-table.py --xbe "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" -o inventory
	@echo "wrote inventory/opcode_table.csv"

# The LIPSinc support module in the image, one row per function with the log string that names it.
lipsinc-runtime:
	@$(PYTHON) tools/lipsinc-runtime.py --xbe "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" -o inventory
	@echo "wrote inventory/lipsinc_runtime.csv"

# The LIPSinc lip-sync directory: the text phoneme maps and the .lad containers (docs/formats/lipsinc-lad.md).
lipsinc-index:
	@$(PYTHON) tools/lipsinc-index.py --dir "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/System/LIPSincData" -o inventory
	@echo "wrote inventory/lipsinc_files.csv, inventory/lipsinc_phonemes.csv, inventory/lipsinc_lad.csv"

# Every function's FunctionFlags, read from its export payload (docs/systems/NETWORK.md). Needs the packages.
function-flags:
	@$(PYTHON) tools/function-flags.py --packages "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/System" -o inventory
	@echo "wrote inventory/function_flags.csv"

# The same table's consistency, and the disassembler that consumes it. Committed files only.
opcode-table-check:
	@$(PYTHON) tools/opcode-table.py --check
	@$(PYTHON) tools/uscript-dis.py --selftest

# Every class against the content that instantiates it (docs/CUT_CONTENT.md, PLAN 5.6). Committed CSVs only.
class-reach:
	@$(PYTHON) tools/class-reach.py -o inventory
	@echo "wrote inventory/class_reach.csv"

# The same pass, written into the extracted source tree so 3.6 has one tree with text and defaults.
defaults-tree:
	@$(PYTHON) tools/defaults-emit.py "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/System" \
		--emit "$(WORK)/defaults" --merge "$(WORK)/source-tree"

# --- artifact stages that had no target -------------------------------------------------------------------------
# docs/ARTIFACTS.md audits this: an artifact no command regenerates cannot be checked, so it drifts silently.

# Dump-free: reads four committed inventory CSVs and writes a fifth, so `make ci` insists it reproduces.
campaign-matrix:
	@$(PYTHON) tools/campaign-matrix.py -o inventory
	@echo "wrote inventory/campaign_matrix.csv"

# Dump-side: the placed-class census needs the map actor tables and the decompiled scripts.
content-census:
	@SG1_DUMP="$(SG1_DUMP)" $(PYTHON) tools/content-census.py --src "$(WORK)/UnrealScript" -o inventory
	@echo "wrote inventory/content_census.csv"

dialogue-index:
	@$(PYTHON) tools/dialogue-index.py "$(SG1_DUMP)" -o inventory
	@echo "wrote inventory/dialogue_by_mission.csv"

# The property census reads ExportAt output, which only a Ghidra run produces.
property-types:
	@$(PYTHON) tools/property-map.py --exports "$(WORK)/native" -o inventory
	@echo "wrote inventory/property_types.csv"

# Calibrated against the privately held reference decompile: without it, stop rather than publish a guess.
token-map:
	@if [ -z "$(ORACLE_DIR)" ]; then echo "token-map: set ORACLE_DIR to the reference decompile" >&2; exit 2; fi
	@$(PYTHON) tools/token-calibrate.py --oracle "$(ORACLE_DIR)" --src "$(WORK)/UnrealScript" -o inventory
	@echo "wrote inventory/token_map.csv"

game-modules:
	@$(PYTHON) tools/game-modules.py --index "$(WORK)/native/functions.csv" -o inventory/xbe_game_modules.csv
	@echo "wrote inventory/xbe_game_modules.csv"

package-check:
	@$(PYTHON) tools/package-check.py "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/System" -o inventory
	@echo "wrote inventory/package_check.csv"

# The .NET-side artifact: same dump, different reader.
maps-parse: $(TOOL)
	@$(DOTNET) $(TOOL) maps --dump "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15" -o inventory
	@echo "wrote inventory/map_parse.csv"

# The expected hashes of every committed artifact (PLAN 8.1). Run after regenerating anything; `make ci` verifies
# the manifest against the files, which is what catches an artifact edited by hand instead of regenerated.
artifact-hashes:
	@$(PYTHON) tools/artifact-hashes.py
	@echo "wrote inventory/artifact_hashes.csv"

# PLAN 0.5: the lock is checkable. CI verifies its shape; --strict verifies a machine that has the toolchain.
versions:
	@$(PYTHON) tools/versions-check.py
	@echo "a mismatch is a reproduction finding: update tools/versions.lock in its own commit"

# PLAN 6.2: the code behind the check file, so "bypass" has an address instead of an intention.
package-check-code:
	@$(PYTHON) tools/package-check-code.py --xbe "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" -o inventory
	@echo "wrote inventory/package_check_code.csv"

# U5: the save surface read out of the scripts the packages carry. Needs the extraction step first.
savegame-paths:
	@test -d "$(WORK)/UnrealScript" || { echo "savegame-paths: run the extraction first (tools/pkg-script-text.py --all)" >&2; exit 2; }
	@$(PYTHON) tools/savegame-paths.py --src "$(WORK)/UnrealScript" -o inventory
	@echo "wrote inventory/savegame_paths.csv"
