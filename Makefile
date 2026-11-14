# Stargate SG-1: The Alliance — decompilation project
#
# The game dump is never part of this repo. Point SG1_DUMP at your own copy:
#   make verify-dump SG1_DUMP=/path/to/dump
# game-derived output lives here, outside the repo
SG1_DUMP ?= $(HOME)/sg1/dump
# the game directory, and the package directory every package-side target reads; both were appended inline in
# some targets and left undefined in others (`make pkg-rebuild` with no SYSTEM_DIR ran the writer on "")
GAME_DIR   ?= $(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15
SYSTEM_DIR ?= $(GAME_DIR)/System
WORK     ?= $(HOME)/sg1/work
GHIDRA_HOME ?= $(HOME)/tools/ghidra/ghidra_12.2_PUBLIC
PYTHON   ?= python3
VA       ?= 0x53f770
RESIZE_BYTES ?= 64
UE2RT    ?= $(HOME)/tools/ue2rt/x
# the privately held reference decompile, only the token map needs it
ORACLE_DIR ?=
DOTNET   ?= dotnet
TOOL     := tools/Sg1Tool/bin/Release/net10.0/Sg1Tool.dll

.PHONY:body-name library-bodies defaults-misses compile-readiness-doc campaign-matrix content-census dialogue-index property-types token-map game-modules package-check package-check-code package-check-pairs package-check-quick package-check-emit karma-runtime module-split gnatives-writes gnatives-stubs gnatives-name-stubs native-channels savegame-paths console-commands hud-messages hud-text interaction-setup maps-parse artifact-hashes versions class-reach rebuild rebuild-hybrid rebuild-full serialize-slots opcode-table opcode-table-check payload-tail function-flags lipsinc-index lipsinc-frames lipsinc-parse lipsinc-tracks lipsinc-keys lipsinc-groups lipsinc-sampler lipsinc-weights lipsinc-bones lipsinc-bone-owners lipsinc-runtime opcode-refusals native-reconcile native-bodies native-census native-operators native-table opcode-usage defaults-emit defaults-tree defaults-coverage defaults-per-class help pkg-verify pkg-rebuild pkg-rebuild-all pkg-layout pkg-resize verify-dump manifest inventory natives call-census formats decompile oracle-diff ghidra class-tables class-overrides class-names symbols libraries assert-residual status checkpoints fork-surface ucpp-vtables release-notes class-graph package-names nightly ci check clean ue2rt-probe sgs-profile argc-pass sgs-write module-attribution module-lists module-census body-read class-ctors class-table-parents registration-sites gnatives-records gnatives-mission map-loads vehicle-seats voice-packs voice-sets voice-descs class-records weapon-links weapon-effective native-opcodes switch-operands  vcall-field-owner class-table-residual live-class-tables live-class-tables-m00 class-scripttext-ref vcall-dispatch field-objects native-vtable ai-states ai-state-body state-exports class-tail class-prefix class-record-fields class-record-middle class-record-run class-record-walkback class-record-array class-record-element-fields class-record-element class-record-tail class-record-gap record-writer-calls archive-slots class-member-map member-access-census class-array-head class-config-pair member-assert-names class-record-order class-record-gap-front class-record-bound defaults-residue delegate-defaults defaults-blockstart defaults-blockstart-census class-record-room class-tail-units class-tail-run class-record-overshoot class-overshoot-stream class-record-array-head class-record-array-search class-record-array-head class-record-array-search class-record-array-blind class-record-front class-record-open-front class-record-open-tail class-record-no-shape class-blockstart-guard class-group-stop class-group-after-scan class-defaults-moved class-defaults-silent class-defaults-single matching-baseline refusal-candidates map-frames checkpoint-loads save-path-compose path-literals fmgr-dispatch fmgr-live-object fmgr-installs linker-purge

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
	@echo "  class-overrides  which slots each class replaces relative to its parent (needs the dump)"
	@echo "  class-names   name those tables from the build's class registrations (needs capstone)"
	@echo "  symbols       emit symbols/xbox.map and the assembler form a debugger loads"
	@echo "  libraries     list the libraries the XBE was linked against (PLAN 2.6)"
	@echo "  squad-rosters  read every SQUAD_* roster class (needs the dump)"
	@echo "  pkg-verify    re-encode every package's tables and diff against the original"
	@echo "  pkg-rebuild   rebuild whole packages from header + tables + payloads and diff"
	@echo "  pkg-rebuild-all  the same over .u, .sgs and .unr — every container kind the build ships"
	@echo "  pkg-layout    census of where each region sits in a package"
	@echo "  pkg-resize    re-lay every package with one payload resized and round-trip it"
	@echo "  compile-readiness  per-class check of what a rebuild would have (3.6 inputs)"
	@echo "  map-census    regenerate the map-side census CSVs (needs the dump)"
	@echo "  package-census  regenerate the four package-side census CSVs (needs the dump)"
	@echo "  inventory-index  index every committed artifact and what regenerates it"
	@echo "  status        regenerate the progress table in README.md and docs/STATUS.md"
	@echo "  checkpoints   roll the .sgs schema up per class (PLAN 8.3, checkpoint system)"
	@echo "  sgs-profile   reconcile the profile's [Saved Games] index with the checkpoints on disc"
	@echo "  argc-pass     read a native's arity, calling convention and call sites (VA=0x0006f4b0)"
	@echo "  sgs-write     rebuild a checkpoint package and add its [Saved Games] row (needs the dump)"
	@echo "  fork-surface  list every class the fork inserts onto a stock UE2.5 base (PLAN 4.8)"
	@echo "  ucpp-vtables  resolve the class tables of the six ucpp-native classes (PLAN 4.7)"
	@echo "  release-notes generate the body for a milestone release (MILESTONE=a..f)"
	@echo "  class-graph   walk the subclass tree under a root (ROOT=BotBase) with states and usage"
	@echo "  ai-states     read every AI controller state out of the shipped text into inventory/ai_states.csv"
	@echo "  ai-state-body walk each AI state body as a statement tree into inventory/ai_state_bodies.csv"
	@echo "  state-exports every AI state export and the states no class text declares"
	@echo "  package-names parse every .u header and name table (PLAN 5.2/4.7 prerequisite)"
	@echo "  nightly       run the dump-free gate plus every dump-side check, one summary per run (PLAN 7.5)"
	@echo "  pkg-exports   list a class's exports and their sizes from a package (PKG=... CLASS=...)"
	@echo "  extract-script write every class's shipped source into $WORK (never the repo)"
	@echo "  ue2rt-probe   can a UE2 compiler read this build? (docs/COMPILER.md)"
	@echo "  map-actors    histogram every .unr's export classes (PLAN 5.2, class-level)"
	@echo "  sfx-index     count the Sound exports in every .uax (PLAN 5.3)"
	@echo "  prop-stream   read one map export's property block (MAP=... EXPORT=...)"
	@echo "  t3d           write every map's actors as T3D into \$$WORK/extracted/maps (PLAN 5.2)"
	@echo "  class-defaults read one class's default properties (PKG=... CLASS=...)"
	@echo "  defaults-emit  render every class's defaultproperties block (PLAN 3.5, needs the dump)"
	@echo "  defaults-coverage  roll the defaults locator up per package (needs the dump)"
	@echo "  defaults-per-class per-class defaults rows: block size, declared fraction (needs the dump)"
	@echo "  class-records where a class payload stops being a record (needs the dump)"
	@echo "  class-record-run  the stride-9 record array's head: is a count in front of it (needs the dump)"
	@echo "  class-record-walkback  walk back from the run at the record's own widths (needs the dump)"
	@echo "  class-record-array     read the record array at the writer's position: head, tail, residue (needs the dump)"
	@echo "  class-record-array-head  the same array at the writer's head, and the record closed from it (needs the dump)"
	@echo "  class-record-array-search  the head located from the block side, with the gap census as the control (needs the dump)"
	@echo "  class-record-array-blind  the head located by walking the element grid back from the block start, no group (needs the dump)"
	@echo "  class-record-element-fields  three compact fields per element, against the shipped walk (needs the dump)"
	@echo "  class-record-element   restart the array grammar at its own tail: element span, second parse, control"
	@echo "  class-record-tail      the bytes after the array: a count of name/reference pairs, and its per-package share"
	@echo "  class-record-gap       split the gap after the array: a second class-reference array, then the list"
	@echo "  class-record-order     where the middle's writers put its fields: the offset sweep and the pair's position (needs the dump)"
	@echo "  class-record-gap-front what the 19-21 bytes in front of the array hold: a compact front and sixteen zero bytes (needs the dump)"
	@echo "  class-record-bound    the array head after the sixteen zero bytes, as a bound on the block start (needs the dump)"
	@echo "  class-record-open-front the gap shape at prefix+42 on the 578 rows whose walk does not close (needs the dump)"
	@echo "  class-record-open-tail  the closing group read from the element end where the block start is inside it (needs the dump)"
	@echo "  class-record-no-shape   the 63 open rows with no shape at prefix+42, scanned for a later head (needs the dump)"
	@echo "  class-blockstart-guard  the block-start chooser refusing a start inside the closing group (needs the dump)"
	@echo "  class-group-stop        what opens at the closing group end, on the rows the chooser refuses (needs the dump)"
	@echo "  class-group-after-scan  which offset past the closing group opens the block, on the 13 rows nothing opens at its end (needs the dump)"
	@echo "  class-defaults-moved   where the starts that move without naming more sit against the closing group: 58 rows, 36 to no block (needs the dump)"
	@echo "  class-defaults-silent  the 196 zero-fraction starts: 374 of 440 tags name no property, and no offset of those payloads holds a declared block (needs the dump)"
	@echo "  class-defaults-single  the guard adds 88 one-tag starts and the locator publishes 288: both walk as one declared tag with nothing after it (needs the dump)"
	@echo "  class-record-front    the 3-5 byte front against four readings and three controls: a name, a tag, a reference, a width (needs the dump)"
	@echo "  class-record-room     the 32 impossible block starts, re-located by the record fields (needs the dump)"
	@echo "  class-tail-units      the record tail's units, and whether the 32 claims start inside one (needs the dump)"
	@echo "  class-tail-run        the run after the last unit: names, an import reference, a name, a list; where the census starts"
	@echo "  class-record-overshoot  the arrays that end past the block start, against the locator head census"
	@echo "  class-overshoot-stream  the defaults stream read from the block start, on the rows the head census is silent on, and at every offset inside a short row's trailer"
	@echo "  archive-slots          the archive's vtable and one compact field per FName, measured from its slots"
	@echo "  record-writer-calls    every call into the record and name writers, and the member each is handed"
	@echo "  class-member-map       where a class's members begin, read from its destructor's lea/call chain"
	@echo "  member-access-census  every access to a member displacement, and the stride it indexes at"
	@echo "  class-array-head      the three head dwords of the Class +0x4c4 array, and who writes them"
	@echo "  class-config-pair     the Class +0x4b0/+0x4b4 pair: the class reference and the config name"
	@echo "  member-assert-names   the member displacements the image names in its own assert expressions"
	@echo "  class-reach   every class vs the content that instantiates it (PLAN 5.6, no dump needed)"
	@echo "  native-reconcile  script native() declarations vs the native records (PLAN 3.9, needs the dump)"
	@echo "  native-bodies mark every native body in the extracted tree + the cross-link table (PLAN 3.9, needs the dump)"
	@echo "  native-census which of the registration table's body addresses are function starts (PLAN 3.9, needs the image)"
	@echo "  native-operators which record each operator declaration composes (PLAN 3.9, needs the dump)"
	@echo "  rebuild       run the rebuild pipeline stage by stage (PLAN 6.1; needs the dump)"
	@echo "  serialize-slots  every class's Serialize override from its class table (PLAN 3.7; needs the dump)"
	@echo "  opcode-table  the walker's opcode map and per-group operand rules (PLAN 3.7; needs the dump)"
	@echo "  opcode-table-check  validate the committed table with no image (what make ci runs)"
	@echo "  opcode-usage  which opcodes the shipped scripts that walk clean actually use (needs the dump)"
	@echo "  switch-operands  what a 0x05 switch's operand byte is: the width of the value it compares"
	@echo "  script-calls  every call site in the corpus, static or by name (needs the dump)"
	@echo "  decompiler-gaps  the reconstruction against the shipped source, per class (needs both trees)"
	@echo "  payload-tail  every Function payload's tail, checked against its script (docs/BYTECODE.md; needs the dump)"
	@echo "  rebuild-hybrid  our scripts + original assets: 18 assembled packages, a manifest, a missions list (PLAN 6.4)"
	@echo "  rebuild-full    + compile/repack/check/boot, which report SKIP with their reason"
	@echo "  function-flags  every function's FunctionFlags from its export payload (docs/systems/NETWORK.md; needs the dump)"
	@echo "  lipsinc-index  the LIPSinc .map phonemes and .lad containers (docs/formats/lipsinc-lad.md; needs the dump)"
	@echo "  lipsinc-frames the .lad body as frames of one 32-byte record per channel (needs the dump)"
	@echo "  lipsinc-parse  the whole .lad replayed in the loader's order, every byte accounted for (needs the dump)"
	@echo "  lipsinc-tracks the .lad body past the pose frames: named clips, then a track table (needs the dump)"
	@echo "  lipsinc-keys   each track's key block: 26 groups of (time, value) keys (needs the dump)"
	@echo "  lipsinc-groups the 26 groups as a slot table: weights vs signed values, redundant pairs (needs the dump)"
	@echo "  opcode-refusals the 85 scripts the bytecode walker still refuses, one row each (needs the packages)"
	@echo "  matching-baseline per package, the functions a rebuild must match and the ones the comparator can grade"
	@echo "  refusal-candidates which single table row would recover the most refused scripts (one variable at a time)"
	@echo "  lipsinc-sampler  what the runtime answers where a curve has no keys: hold, not extrapolation (needs the dump)"
	@echo "  lipsinc-weights  which slots the consumer applies: a non-zero value, and 65.5% of them are zero (needs the dump)"
	@echo "  lipsinc-bones  the channel table as an ordered bone list, and the pose record's angle (needs the dump)"
	@echo "  lipsinc-bone-owners  which packages declare each .lad channel name as a bone (needs the dump)"
	@echo "  lipsinc-runtime  the LIPSinc support functions, from their own log strings (needs the dump)"
	@echo "  campaign-matrix  missions x maps x speech x music x chapters (dump-free: reads committed CSVs)"
	@echo "  content-census   which game classes are placed in a map (needs the dump and WORK/UnrealScript)"
	@echo "  dialogue-index   speech lines per mission, from the Speech/ WAV names (needs the dump)"
	@echo "  property-types   UnProp property-type census from ExportAt output (needs WORK/native)"
	@echo "  token-map        the bytecode token map calibrated against the private tree (needs ORACLE_DIR)"
	@echo "  game-modules     per-module function counts from a native index (needs WORK/native)"
	@echo "  package-check    the Packages.md5 check objects (needs the dump)"
	@echo "  package-check-code  the functions that read the check file, from the image (needs the dump)"
	@echo "  package-check-pairs  pair each check record with the package its GUID names (needs the dump)"
	@echo "  package-check-quick  reproduce every check record's quick digest from header + names (needs the dump)"
	@echo "  package-check-emit  write a Packages.md5 of our own, and reproduce the shipped one byte for byte (needs the dump)"
	@echo "  savegame-paths   the save/load call sites, read from the shipped ScriptText (needs the dump)"
	@echo "  console-commands the console's exec-function surface, from the scripts and the binary's Exec flag"
	@echo "  interaction-setup  every Interaction the game installs on the player, and the routine that does it"
	@echo "  karma-runtime    the Karma (ragdoll physics) functions, from their own strings (needs the dump)"
	@echo "  module-split     per-function module attribution, the per-module export plan (dump-free)"
	@echo "  gnatives-writes  the GNatives slots the image writes itself, and the value in each (needs the dump)"
	@echo "  gnatives-records  the storeless slots, named by the {name, body} record that carries the body (needs the dump)"
	@echo "  gnatives-stubs    the storeless slots, from the stub that pushes its own slot index (needs the dump)"
	@echo "  gnatives-name-stubs  the stubs that push -1: the name-registered natives (needs the dump)"
	@echo "  gnatives-mission  the diff between the menu-time GNatives dump and one taken after a mission loads"
	@echo "  native-channels  which channel registers each index-less native: stub, record, store or nothing"
	@echo "  module-lists     one address list per module for ExportAt.java, under $(WORK)"
	@echo "  module-attribution  which translation unit an unnamed function belongs to, by address locality"
	@echo "  class-ctors     every class constructor and destructor, from the class table each installs"
	@echo "  class-table-parents  name a class table from the callers of the function that installs it"
	@echo "  registration-sites  every class-constructor call site and the class literal it pushes (needs the dump)"
	@echo "  module-census    what the exported bodies are: section, fan-in, literals (needs the dump and the export)"
	@echo "  body-read        exported bodies of D3DDrv and XboxDrv: translation unit, literals, callee modules, slots (needs the export)"
	@echo "  library-bodies   the library sections' own evidence: strings, source paths, RTTI, direct calls (needs the dump)"
	@echo "  body-name        which bodies name themselves from their own literals or a byte-identical twin (needs the export)"
	@echo "  defaults-misses  why each class has no fully declared defaults block (needs the dump)"
	@echo "  defaults-residue the values the defaults renderer refuses, as shape rows (needs the dump)"
	@echo "  delegate-defaults a delegate default decoded as {object, function} (needs the dump)"
	@echo "  defaults-blockstart where a delegate tag sits, and whether its value is a record run (needs the dump)"
	@echo "  defaults-blockstart-census every located block start tested against a shifted control (needs the dump)"
	@echo "  maps-parse       map summary from the package headers (needs the dump and Sg1Tool)"
	@echo "  artifact-hashes  the expected SHA-256 of every committed artifact (checked in make ci)"
	@echo "  versions         check tools/versions.lock against this machine (--strict to fail on a mismatch)"
	@echo "  assert-residual  classify the assert sites that are still in no function (needs asserts.tsv)"
	@echo "  hud-console-font  which font the console's input line is drawn in (needs the dump; no boot)"
	@echo "  hud-messages     which HUD class draws which message, from the scripts (needs the dump; no boot)"
	@echo "  hud-text         read the HUD's three text boxes over a live stub (needs a boot: tools/xemu/hud-text.py)"
	@echo "  map-frames       read each PASS row's post-load frame: the HUD corner, into inventory/map_frames.csv"
	@echo "  checkpoint-loads load a shipped .sgs inside its own level and read whether play resumes"
	@echo "  save-path-compose which path the checkpoint loader composes, and where the shipped .sgs are"
	@echo "  path-literals    every path-shaped literal in the image, and the slots of the file manager table"
	@echo "  fmgr-dispatch    which slots of that table the image calls, and what each body answers"
	@echo "  fmgr-live-object which object the global holds: the singleton row, its table, and every access"
	@echo "  fmgr-installs    every store to that global, the frame slot it points at, and the phase each belongs to"
	@echo "  linker-purge     what the array the file-manager purge walks holds, and why its per-element cast is an identity"
	@echo "  vehicle-seats    read the seat/turret wiring out of the default properties into inventory/vehicle_seats.csv"
	@echo "  voice-packs      which pack each class under StargatePawn speaks with, into inventory/voice_packs.csv"
	@echo "  voice-sets       how many voices each pack holds, into inventory/voice_sets.csv"
	@echo "  voice-descs     what a per-sound VoiceDesc holds, into inventory/voice_descs.csv"
	@echo "  weapon-links    which class each weapon-tree class names in its defaults, into inventory/weapon_links.csv"
	@echo "  weapon-effective  the same graph resolved through the class chain, into inventory/weapon_effective.csv"
	@echo "  native-opcodes  the slots the native-call opcodes above 0x5f carry, into inventory/native_opcodes.csv"
	@echo "  native-binder    the names the binder resolves index-less natives from (needs the dump; no boot)"
	@echo "  native-table     each of those names' body address, from the image's own registration table (needs the dump; no boot)"
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

# U7's static half: the GNatives slots the image writes itself and the value each store carries. No boot and
# no Ghidra -- it reads the store instructions, resolves each value through the pointer table they load from,
# and reconciles the result with the runtime dump in inventory/gnatives_slots.csv.
gnatives-writes:
	@test -f "$(GAME_DIR)/default.xbe" || { echo "no default.xbe under $(GAME_DIR)"; exit 2; }
	@$(PYTHON) tools/gnatives-writes.py --xbe "$(GAME_DIR)/default.xbe" -o inventory
	@echo "wrote inventory/gnatives_write_sites.csv"

# U7's other half: a slot `gnatives-writes` cannot see is not a slot without evidence. Every native body also
# sits in a `{pool name, body}` record in .data, so pairing the live slots with those records names the blind
# ones (docs/NATIVE_VM.md). Reads the image and two committed CSVs; no boot.
gnatives-records:
	@test -f "$(GAME_DIR)/default.xbe" || { echo "no default.xbe under $(GAME_DIR)"; exit 2; }
	@$(PYTHON) tools/gnatives-records.py --xbe "$(GAME_DIR)/default.xbe" -o inventory

# U7's third shape: each of the 123 storeless slots has its own registration stub in .text -- `push &body;
# push <index>; call 0x46c60` -- and the registrar does the `mov [eax*4+0x6313e8], edx` a store scan cannot see.
# Reads the image and two committed CSVs; no boot, no Ghidra.
gnatives-stubs:
	@test -f "$(GAME_DIR)/default.xbe" || { echo "no default.xbe under $(GAME_DIR)"; exit 2; }
	@$(PYTHON) tools/gnatives-stubs.py --xbe "$(GAME_DIR)/default.xbe" -o inventory

# The same stub shape with `push -1` instead of an index: the name-registered natives. The registrar returns for -1,
# so these 467 bodies reach no slot at the main menu; each one carries a `{name, body}` record.
gnatives-name-stubs:
	@test -f "$(GAME_DIR)/default.xbe" || { echo "no default.xbe under $(GAME_DIR)"; exit 2; }
	@$(PYTHON) tools/gnatives-name-stubs.py --xbe "$(GAME_DIR)/default.xbe" -o inventory

# The menu-time side of the diff is the committed map, not a scratch dump: `$WORK/gnatives/runtime.bin` is pruned
# with the rest of a run's scratch, and the map carries the same index -> address pairs. Point MENU_BASELINE at a
# raw 16 KB dump only to compare two boots.
MENU_BASELINE ?= inventory/gnatives_slots.csv
MISSION_DUMP  ?= $(WORK)/mission-gnatives/index-10/gnatives.bin

# U7's second snapshot: the same 16 KB table read out of a guest that has a mission loaded (the first was taken
# at the main menu). Both dumps stay under $(WORK) -- they are guest memory -- and only the diff is committed:
# which slots the mission brought in, and through which channel the image registers them. Produce the second
# dump with `tools/xemu/gnatives-mission.sh 10` (one boot, M00); this target then reads both.
gnatives-mission:
	@test -f "$(GAME_DIR)/default.xbe" || { echo "no default.xbe under $(GAME_DIR)"; exit 2; }
	@test -f "$(MENU_BASELINE)" || { echo "no menu baseline at $(MENU_BASELINE)"; exit 2; }
	@test -f "$(MISSION_DUMP)" || { \
		echo "no mission dump at $(MISSION_DUMP) -- run tools/xemu/gnatives-mission.sh 10 first"; exit 2; }
	@$(PYTHON) tools/gnatives-mission.py "$(GAME_DIR)/default.xbe" \
		--menu "$(MENU_BASELINE)" --mission "$(MISSION_DUMP)" -o inventory

# Which channel registers each of the 523 index-less `native` declarations: a `push -1` stub, the `{name, body}`
# record the name lookup binds from, or the class's store-registered slots. The 56 with no stub resolve as 41
# `Object` records and 15 declarations in packages the image does not carry (docs/NATIVE_VM.md).
native-channels:
	@test -f "$(GAME_DIR)/default.xbe" || { echo "no default.xbe under $(GAME_DIR)"; exit 2; }
	@$(PYTHON) tools/native-channels.py --xbe "$(GAME_DIR)/default.xbe" -o inventory

# Where each class object is built: every `E8 rel32` in the executable sections whose target is the class
# constructor `FUN_0003af60`, and the class literal each site pushes. No boot and no Ghidra. The class table
# names 466 of these sites and is the check on the scan, not its input (docs/NATIVE_CLASSES.md).
registration-sites:
	@test -f "$(GAME_DIR)/default.xbe" || { echo "no default.xbe under $(GAME_DIR)"; exit 2; }
	@$(PYTHON) tools/registration-sites.py "$(GAME_DIR)/default.xbe" -o inventory/registration_sites.csv
	@echo "wrote inventory/registration_sites.csv"

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

# The checkpoint index lives in the profile, not in the package: `[Saved Games]` maps a file name to the
# description the menu shows, so a rebuilt checkpoint needs a row here as well as a file on disc.
sgs-profile:
	@prof="$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/System/Profiles/DefaultProfile/DefaultProfile.sgp"; \
	test -f "$$prof" || { echo "no profile at $$prof (SG1_DUMP=$(SG1_DUMP))"; exit 2; }; \
	$(PYTHON) tools/sgs-profile.py "$$prof"

# A native's arity is in the code, not in the package: the frame slots it reads name the last argument,
# `ret imm16` says who removes them, and its call sites say how many dwords were pushed. This is the
# number a rebuilt caller needs, and `--check` keeps the decoder honest without the dump.
argc-pass:
	@test -d "$(SG1_DUMP)" || { echo "SG1_DUMP not found: $(SG1_DUMP)"; exit 2; }; \
	$(PYTHON) tools/argc-pass.py --va $(or $(VA),0x0006f4b0) \
		--dump "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15"

# The write side of a checkpoint is two artefacts, so this target writes both and then reads them back: the
# package is rebuilt from the parsed model, the `[Saved Games]` row goes into a copy of the profile. It reports
# where a rebuild stops rather than guessing, and exits 1 only when a file parses and still rebuilds differently.
sgs-write:
	@test -d "$(SG1_DUMP)" || { echo "SG1_DUMP not found: $(SG1_DUMP)"; exit 2; }; \
	$(PYTHON) tools/sgs-write.py --dump "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15" \
		--out "$(WORK)/sgs-write"

# Needs the privately held 2006 Classes tree; deliberately not part of `make inventory`.
oracle-diff:
	@test -n "$(ORACLE_DIR)" || { echo "set ORACLE_DIR=/path/to/private/Classes (never committed)"; exit 2; }
	@$(PYTHON) tools/oracle-diff.py --oracle "$(ORACLE_DIR)" --src "$(WORK)/UnrealScript" -o inventory

# Everything CI can check without the dump: CSV shapes, the function and symbol maps, and that the
# generated docs reproduce. AGENTS.md forbids CI from downloading the game, so this is the whole of the
# per-commit gate; the dump-side checks stay in `make check` and `make ghidra`.
ci:
	@$(PYTHON) tools/ci.py
	@$(PYTHON) tools/xemu/map-loads.py --check-doc docs/MAP_LOADS.md
	@$(PYTHON) tools/xemu/map-frame.py --check-doc docs/MAP_FRAMES.md
	@$(PYTHON) tools/xemu/checkpoint-load.py --check-doc docs/systems/CHECKPOINTS.md
	@$(PYTHON) tools/save-path-compose.py --check-doc docs/systems/CHECKPOINTS.md
	@$(PYTHON) tools/path-literals.py --check-doc docs/systems/CHECKPOINTS.md
	@$(PYTHON) tools/fmgr-dispatch.py --check-doc docs/systems/CHECKPOINTS.md
	@$(PYTHON) tools/fmgr-live-object.py --check-doc docs/systems/CHECKPOINTS.md
	@$(PYTHON) tools/fmgr-installs.py --check-doc docs/systems/CHECKPOINTS.md
	@$(PYTHON) tools/linker-purge.py --check-doc docs/systems/CHECKPOINTS.md
	@$(PYTHON) tools/vehicle-seats.py --check-doc docs/systems/VEHICLES.md
	@$(PYTHON) tools/ai-states.py --check-doc docs/systems/AI.md
	@$(PYTHON) tools/ai-state-body.py --check-doc docs/systems/AI.md
	@$(PYTHON) tools/state-exports.py --check-doc docs/systems/AI.md
	@$(PYTHON) tools/voice-packs.py --check-doc docs/systems/AUDIO.md
	@$(PYTHON) tools/voice-sets.py --check-doc docs/systems/AUDIO.md
	@$(PYTHON) tools/voice-descs.py --check-doc docs/systems/AUDIO.md
	@$(PYTHON) tools/weapon-links.py --check-doc docs/systems/WEAPONS.md
	@sh -n tools/build.sh && echo "ok   build-pipeline: tools/build.sh parses"
	@$(PYTHON) -m py_compile tools/xemu/mem.py tools/xemu/bootcheck.py tools/xemu/drive.py \
		tools/xemu/console-line.py tools/xemu/map-index.py tools/xemu/frame-diff.py \
		tools/xemu/objects.py tools/xemu/ramscan.py tools/xemu/vamap.py tools/xemu/console-state.py \
		tools/xemu/owner.py tools/xemu/names.py tools/xemu/fields.py tools/xemu/hud-link.py \
		tools/xemu/map-loads.py tools/xemu/checkpoint-load.py \
		&& echo "ok   xemu-tools: mem.py, bootcheck.py, drive.py, console-line.py, map-index.py, frame-diff.py, objects.py ramscan.py, vamap.py, console-state.py, owner.py, names.py, fields.py, hud-link.py and map-loads.py compile"
	@$(PYTHON) tools/xemu/ramscan.py --selftest
	@$(PYTHON) tools/xemu/hud-link.py --selftest
	@$(PYTHON) tools/xemu/map-loads.py --check
	@$(PYTHON) tools/xemu/checkpoint-load.py --check
	@$(PYTHON) tools/save-path-compose.py --check
	@$(PYTHON) tools/path-literals.py --check
	@$(PYTHON) tools/fmgr-dispatch.py --check
	@$(PYTHON) tools/fmgr-live-object.py --check
	@$(PYTHON) tools/fmgr-installs.py --check
	@$(PYTHON) tools/linker-purge.py --check
	@$(PYTHON) tools/xemu/mem.py --selftest
	@$(PYTHON) tools/xemu/console-state.py --selftest
	@$(PYTHON) tools/xemu/owner.py --selftest
	@$(PYTHON) tools/xemu/names.py --selftest
	@$(PYTHON) tools/xemu/fields.py --selftest
	@$(PYTHON) tools/xemu/sink-slots.py --selftest
	@$(PYTHON) -m py_compile tools/xemu/hud-text.py && echo "ok   hud-text: tools/xemu/hud-text.py compiles"
	@$(PYTHON) tools/xemu/hud-text.py --selftest
	@sh -n tools/xemu/hud-say.sh && echo "ok   hud-say: tools/xemu/hud-say.sh parses"
	@$(PYTHON) -m py_compile tools/xemu/qmp-caps.py && echo "ok   qmp-caps: tools/xemu/qmp-caps.py compiles"
	@$(PYTHON) tools/xemu/qmp-caps.py --selftest
	@$(PYTHON) tools/xemu/drive.py --selftest
	@$(PYTHON) -m py_compile tools/lad-scan.py && echo "ok   lad-scan: tools/lad-scan.py compiles"
	@$(PYTHON) -m py_compile tools/hud-console-font.py && echo "ok   hud-console-font: tools/hud-console-font.py compiles"
	@$(PYTHON) tools/hud-console-font.py --selftest
	@$(PYTHON) -m py_compile tools/hud-messages.py && echo "ok   hud-messages: tools/hud-messages.py compiles"
	@$(PYTHON) tools/hud-messages.py --selftest
	@$(PYTHON) -m py_compile tools/native-binder.py && echo "ok   native-binder: tools/native-binder.py compiles"
	@$(PYTHON) tools/native-binder.py --selftest
	@$(PYTHON) -m py_compile tools/native-table.py && echo "ok   native-table: tools/native-table.py compiles"
	@$(PYTHON) tools/native-table.py --check
	@$(PYTHON) -m py_compile tools/native-reconcile.py && echo "ok   native-reconcile: tools/native-reconcile.py compiles"
	@$(PYTHON) tools/native-reconcile.py --selftest
	@$(PYTHON) -m py_compile tools/native-mark.py && echo "ok   native-mark: tools/native-mark.py compiles"
	@$(PYTHON) tools/native-mark.py --selftest
	@$(PYTHON) tools/native-mark.py --check
	@$(PYTHON) -m py_compile tools/native-census.py && echo "ok   native-census: tools/native-census.py compiles"
	@$(PYTHON) tools/native-census.py --selftest
	@$(PYTHON) tools/native-census.py --check
	@$(PYTHON) -m py_compile tools/native-operators.py && echo "ok   native-operators: tools/native-operators.py compiles"
	@$(PYTHON) tools/native-operators.py --selftest
	@$(PYTHON) tools/native-operators.py --check
	@$(PYTHON) -m py_compile tools/map-list-order.py && echo "ok   map-list-order: tools/map-list-order.py compiles"
	@$(PYTHON) tools/map-list-order.py --selftest
	@$(PYTHON) tools/class-reach.py --check
	@$(PYTHON) tools/pkg-write.py --selftest
	@$(PYTHON) tools/inventory-index.py --check
	@$(PYTHON) tools/status.py --check
	@$(PYTHON) tools/status.py --selftest

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

# The same writer over every package kind the build ships: game packages (.u), checkpoints (.sgs) and maps (.unr).
# A container claim that only covers one extension is a claim about that extension, not about the writer.
pkg-rebuild-all:
	@test -d "$(SYSTEM_DIR)" || { echo "SG1_DUMP not found: $(SG1_DUMP)"; exit 2; }
	@$(PYTHON) tools/pkg-write.py "$(SYSTEM_DIR)" --rebuild | tail -2
	@$(PYTHON) tools/pkg-write.py "$(GAME_DIR)/System/Profiles/DefaultProfile" --rebuild --ext .sgs | tail -2
	@$(PYTHON) tools/pkg-write.py "$(GAME_DIR)/Maps" --rebuild --ext .unr | tail -2

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
compile-readiness-doc:
	@$(PYTHON) tools/compile-readiness.py --write-doc docs/COMPILE.md
	@echo "regenerated the tables in docs/COMPILE.md"

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

# Can a UE2 compiler read this build? Drives Wine on an unpacked runtime; writes only under $WORK (docs/COMPILER.md).
ue2rt-probe:
	@$(PYTHON) tools/ue2rt-probe.py --runtime "$(UE2RT)" --work "$(or $(WORK),work)/ue2rt" $(if $(SG1_DUMP),--dump "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15")

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
		--out "$(or $(WORK),work)/extracted/maps" --stats inventory/t3d_coverage.csv

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

# The two build targets (PLAN 6.4, docs/REBUILD.md). `hybrid` places the packages it carries and proves them with a
# manifest; `full` runs every stage and reports the blocked ones with a reason. Both need the dump.
rebuild-hybrid:
	@tools/build.sh --target hybrid

rebuild-full:
	@tools/build.sh --target full

# The locator's own census, and the per-class rows behind it (docs/CLASS_DEFAULTS.md). Both artifacts existed
# without a generator; these targets are what `make inventory-index` now resolves them to.
defaults-coverage:
	@$(PYTHON) tools/class-defaults.py --coverage "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/System" -o inventory
	@echo "wrote inventory/class_defaults_coverage.csv"

defaults-per-class:
	@$(PYTHON) tools/class-defaults.py --packages "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/System" -o inventory
	@echo "wrote inventory/class_defaults_per_class.csv"

# Where a class export stops being a record, and what the payload after it is (docs/CLASS_DEFAULTS.md). Two anchors
# from the export table place the record's prefix on all 2,319 classes; the block the locator found is the record's
# end, so the 109 "no block" rows split into 50 records with no stream and 59 payloads still unexplained. The two
# trailing-byte columns are a control, not an instrument: every class payload ends in 00, so a tail byte locates no end.
class-records:
	@$(PYTHON) tools/class-record.py "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/System" --sweep -o inventory
	@$(PYTHON) tools/class-record.py --report -o inventory
	@echo "wrote inventory/class_records.csv"

# Native declarations in the shipped script against inventory/natives.csv (docs/NATIVE_CLASSES.md, PLAN 3.9).
# Writes three artifacts: the class-by-class reconciliation, the per-record token basis and the residual verdicts.
native-reconcile:
	@$(PYTHON) tools/native-reconcile.py "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/System" -o inventory
	@echo "wrote inventory/native_reconcile.csv, inventory/native_tokens.csv and inventory/native_residual.csv"

# Mark every native body in the extracted script tree and write the cross-link table (PLAN 3.9,
# docs/NATIVE_CLASSES.md). The tree is game-derived and goes to $(WORK); what is committed is the table,
# whose `marker` column is the exact line written above each declaration.
native-bodies:
	@$(PYTHON) tools/native-mark.py "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/System" -o inventory \
		--tree "$(WORK)/UnrealScript-marked"
	@echo "wrote inventory/native_bodies.csv and $(WORK)/UnrealScript-marked"

# Which record each `operator` declaration composes, and whether the registration table has it
# (docs/NATIVE_CLASSES.md, PLAN 3.9). Reads the shipped text, so it needs the dump.
native-operators:
	@$(PYTHON) tools/native-operators.py "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/System" -o inventory
	@echo "wrote inventory/native_operators.csv"

# Which of the registration table's body addresses are function starts, and what the call-graph census is
# short by (docs/NATIVE_CLASSES.md, PLAN 3.9). The image half needs default.xbe; the disassembler verdicts come
# from tools/ghidra/ghidra_scripts/NativeCensus.java and are carried over from the committed table when absent.
native-census:
	@$(PYTHON) tools/native-census.py --dump "$(SG1_DUMP)" --verdicts "$(WORK)/native-census/verdicts.csv" -o inventory
	@echo "wrote inventory/native_census.csv"

# Which classes override Serialize, and where the code is (docs/NATIVE_VM.md, PLAN 3.7). Needs the image.
class-overrides:
	@$(PYTHON) tools/class-overrides.py --xbe "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" -o inventory

serialize-slots:
	@$(PYTHON) tools/class-serialize-slots.py --xbe "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" -o inventory
	@echo "wrote inventory/class_serialize_slots.csv"

# The script walker's opcode map and per-group operand rules (docs/BYTECODE.md, PLAN 3.7). Needs the image.
opcode-table:
	@$(PYTHON) tools/opcode-table.py --xbe "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" -o inventory
	@echo "wrote inventory/opcode_table.csv"

# The LIPSinc support module in the image, one row per function with the log string that names it.
# PLAN 3.5: the reconstruction against the shipped source, per class. Both trees are game-derived and live under $WORK.
SOURCE_TREE ?= $(WORK)/source-tree
DECOMPILED_TREE ?= $(WORK)/UnrealScript
decompiler-gaps:
	@test -d "$(SOURCE_TREE)" || { echo "decompiler-gaps: no source tree (make extract-script SOURCE_TREE=...)" >&2; exit 2; }
	@$(PYTHON) tools/decompiler-gaps.py "$(SOURCE_TREE)" "$(DECOMPILED_TREE)" -o inventory

script-calls:
	@$(PYTHON) tools/uscript-dis.py --calls "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/System" -o inventory

class-refs:
	@$(PYTHON) tools/uscript-dis.py --class-refs "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/System" -o inventory

opcode-usage:
	@$(PYTHON) tools/uscript-dis.py --usage "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/System" -o inventory

switch-operands:
	@$(PYTHON) tools/uscript-dis.py --switches "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/System" -o inventory
	@echo "wrote inventory/switch_operands.csv"

lipsinc-runtime:
	@$(PYTHON) tools/image-strings.py --topic lipsinc --xbe "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" -o inventory
	@echo "wrote inventory/lipsinc_runtime.csv"

# The LIPSinc lip-sync directory: the text phoneme maps and the .lad containers (docs/formats/lipsinc-lad.md).
lipsinc-index:
	@$(PYTHON) tools/lipsinc-index.py --dir "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/System/LIPSincData" -o inventory
	@echo "wrote inventory/lipsinc_files.csv, inventory/lipsinc_phonemes.csv, inventory/lipsinc_lad.csv"

# The .lad body's frames: 20 frames of one 32-byte record per channel under a 5-byte header (docs/formats/lipsinc-lad.md).
lipsinc-frames:
	@$(PYTHON) tools/lad-frames.py --dir "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/System/LIPSincData" -o inventory
	@echo "wrote inventory/lipsinc_frames.csv"

# The whole `.lad` replayed in the order the loader (`FUN_000bce40`) serialises it: header, channel table,
# pose block, clips, tracks, tail. Every byte has to be consumed (docs/formats/lipsinc-lad.md, U4).
lipsinc-parse:
	@$(PYTHON) tools/lad-parse.py --dir "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/System/LIPSincData" -o inventory
	@echo "wrote inventory/lipsinc_parse.csv"

# The .lad body past the pose frames: a clip section, then a track table of <Map>.<Animation> records
# (docs/formats/lipsinc-lad.md).
lipsinc-tracks:
	@$(PYTHON) tools/lad-tracks.py --dir "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/System/LIPSincData" -o inventory
	@echo "wrote inventory/lipsinc_tracks.csv"

# Each track's key block: 26 groups of (time, value) keys (docs/formats/lipsinc-lad.md, U4).
lipsinc-keys:
	@$(PYTHON) tools/lad-keys.py --dir "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/System/LIPSincData" -o inventory
	@echo "wrote inventory/lipsinc_keys.csv"

# The 26 groups measured against each other: 19 weight slots, 7 signed ones, and the pairs that repeat
# (docs/formats/lipsinc-lad.md, U4).
lipsinc-groups:
	@$(PYTHON) tools/lad-groups.py --dir "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/System/LIPSincData" -o inventory
	@echo "wrote inventory/lipsinc_groups.csv"

# What the sampler answers at a time a curve does not cover: the hold at both ends, the smoothstep
# between keys and the 2^-51 snap (docs/formats/lipsinc-runtime.md, U4).
lipsinc-sampler:
	@$(PYTHON) tools/lad-sampler.py --dir "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/System/LIPSincData" -o inventory
	@echo "wrote inventory/lipsinc_sampler.csv"

# Which slots the consumer applies: a record is sampled at tick 0 and the pose builder runs only on a
# non-zero value, so the count is the corpus's gate (docs/formats/lipsinc-runtime.md, U4).
lipsinc-weights:
	@$(PYTHON) tools/lad-weights.py --dir "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/System/LIPSincData" -o inventory
	@echo "wrote inventory/lipsinc_weights.csv"

# The channel table as an ordered bone list, and the pose record's eighth word as an angle
# (docs/formats/lipsinc-lad.md, U4).
lipsinc-bones:
	@$(PYTHON) tools/lad-bones.py --dir "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/System/LIPSincData" -o inventory
	@echo "wrote inventory/lipsinc_bones.csv"

# Which packages name each `.lad` channel as a bone (docs/formats/lipsinc-runtime.md).
lipsinc-bone-owners:
	@$(PYTHON) tools/lad-bone-owners.py --dump "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15" -o inventory
	@echo "wrote inventory/lipsinc_bone_owners.csv"

# Every script the bytecode walker still refuses, with the offset, the opcode and the case (docs/BYTECODE.md).
opcode-refusals:
	@$(PYTHON) tools/uscript-refusals.py "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/System" -o inventory
	@echo "wrote inventory/opcode_refusals.csv"

# PLAN 3.8's scoreboard: per package, the denominator a rebuild is scored against and the negatives with names
# (docs/MATCHING.md). Needs the packages; the page's block is then rendered from the artifact alone.
matching-baseline:
	@$(PYTHON) tools/uscript-dis.py --baseline "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/System" -o inventory
	@$(PYTHON) tools/uscript-dis.py --write-doc docs/MATCHING.md
	@echo "wrote inventory/matching_baseline.csv, docs/MATCHING.md"

# Which single table row would recover the most refusals? One variable at a time, on the opcode the walk stopped
# on (docs/BYTECODE.md). Prints only; needs the packages.
refusal-candidates:
	@$(PYTHON) tools/uscript-refusals.py --candidates "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/System"

# Every function's FunctionFlags, read from its export payload (docs/systems/NETWORK.md). Needs the packages.
function-flags:
	@$(PYTHON) tools/function-flags.py --packages "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/System" -o inventory
	@echo "wrote inventory/function_flags.csv"

# The same table's consistency, and the disassembler that consumes it. Committed files only.
# The `Function` payload's tail: `NativeToken` and `FunctionFlags` against the script's own declarations, over
# every package (docs/BYTECODE.md, docs/UNKNOWNS.md U13). Needs the dump, so it is not a `make ci` gate.
payload-tail:
	@$(PYTHON) tools/uscript-dis.py --payloads "$(SYSTEM_DIR)"

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
	@$(PYTHON) tools/image-strings.py --topic package-check --xbe "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" -o inventory

package-check-pairs:
	@$(PYTHON) tools/package-check-pairs.py "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15" -o inventory
	@echo "wrote inventory/package_check_code.csv"

# PLAN 6.2: the second field of every record, reproduced from the header and the name table.
package-check-quick:
	@$(PYTHON) tools/package-check-quick.py "$(GAME_DIR)" -o inventory
	@echo "wrote inventory/package_check_quick.csv"

# PLAN 6.2's writer half: assemble the database from the records instead of copying the cook's file.
package-check-emit:
	@$(PYTHON) tools/package-check-emit.py --dump "$(GAME_DIR)"

# U5: the save surface read out of the scripts the packages carry. Needs the extraction step first.
savegame-paths:
	@test -d "$(WORK)/UnrealScript" || { echo "savegame-paths: run the extraction first (tools/pkg-script-text.py --all)" >&2; exit 2; }
	@$(PYTHON) tools/savegame-paths.py --src "$(WORK)/UnrealScript" -o inventory
	@echo "wrote inventory/savegame_paths.csv"

# 7.3/U15: what the console can run at all -- the exec declarations in the shipped scripts, joined to the binary's
# Exec flag. `stat` is in neither, which is why a zero on it is a fact about the engine's handler and not delivery.
console-commands:
	@test -d "$(WORK)/UnrealScript" || { echo "console-commands: run the extraction first (tools/pkg-script-text.py --all)" >&2; exit 2; }
	@$(PYTHON) tools/console-commands.py --src "$(WORK)/UnrealScript" -o inventory

# U15/7.3: what installs a GameHud on the player. `GameHudPtr` is written by `SetupInteractionsFor`, and the same
# routine writes four more fields, so the field walk's one `none` is five. Reads the extracted scripts, no boot.
interaction-setup:
	@test -d "$(WORK)/UnrealScript" || { echo "interaction-setup: run the extraction first (tools/pkg-script-text.py --all)" >&2; exit 2; }
	@$(PYTHON) tools/interaction-setup.py --src "$(WORK)/UnrealScript" -o inventory
	@echo "wrote inventory/interaction_setup.csv"
	@echo "wrote inventory/console_commands.csv"

# The third topic of the same scanner: Karma's runtime, which is what the .ka ragdolls are loaded by.
karma-runtime:
	@$(PYTHON) tools/image-strings.py --topic karma --xbe "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" -o inventory
	@echo "wrote inventory/karma_runtime.csv"

# U1's last open question: the names the binder looks up, and which declarations they can bind (`make native-binder`).
native-binder:
	@test -f "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" || \
		{ echo "native-binder: no default.xbe under $(SG1_DUMP)"; exit 2; }
	@$(PYTHON) tools/native-binder.py "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" -o inventory
	@echo "wrote inventory/native_binder_names.csv"

# U1/U5: the registration table behind that lookup -- every name's body address, no boot (`make native-table`).
native-table:
	@test -f "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" || \
		{ echo "native-table: no default.xbe under $(SG1_DUMP)"; exit 2; }
	@$(PYTHON) tools/native-table.py "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" -o inventory
	@echo "wrote inventory/native_targets.csv"

# PLAN 7.3's last question: what draws `stat fps`, the console's one readable output. No boot; docs/XEMU.md.
stat-fps:
	@$(PYTHON) tools/image-strings.py --topic stat-fps --xbe "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" -o inventory
	@echo "wrote inventory/stat_fps_render.csv"

# PLAN 7.3: which console the live HUD holds, out of a RAM dump (`make hud-link DUMP=~/sg1/work/.../ram.bin`).
hud-link:
	@test -n "$(DUMP)" || { echo "hud-link: pass DUMP=<RAM image from monitor=pmemsave> CR3=$(or $(CR3),0xf000)"; exit 2; }
	@$(PYTHON) tools/xemu/hud-link.py "$(DUMP)" --cr3 $(or $(CR3),0xf000) --csv - -o inventory
	@echo "wrote inventory/hud_console_link.csv"

# PLAN 7.3/U15: which HUD class draws which message -- every class under `Engine.HUD`, what its `Message` and
# `DisplayMessages` bodies sink to, and whether `Super` ever runs. Reads the extracted scripts, so no boot.
hud-text:
	@echo "hud-text: needs a boot with a GDB stub -- tools/xemu/hud-text.py --port <stub> (see docs/HUD_TEXT.md)"
	@echo "          and tools/xemu/hud-say.sh runs one: it boots M00, submits a marker and watches the boxes"

# PLAN 7.2: merge `missions.sh` run directories into one committed load table, named from the disc.
# `missions.sh` truncates its missions.tsv on every invocation, so this is what makes a sweep cumulative.
map-loads:
	@test -n "$(ISO)" || { echo "map-loads: pass ISO=<disc image> RUNS='<missions.sh outdir> ...'" >&2; exit 2; }
	@$(PYTHON) tools/xemu/map-loads.py --iso "$(ISO)" $(RUNS) -o inventory/map_loads.csv

# PLAN 7.2: the question the load table cannot answer -- is a map that loads playable? -- read off the frame the
# verdict was taken from. The HUD's portrait-and-name corner is the witness; nothing here needs a boot.
map-frames:
	@test -d "$(WORK)" || { echo "map-frames: pass WORK=<the sg1 work directory holding the missions* run dirs>" >&2; exit 2; }
	@$(PYTHON) tools/xemu/map-frame.py --work "$(WORK)" -o inventory/map_frames.csv
	@$(PYTHON) tools/xemu/map-frame.py --write-doc docs/MAP_FRAMES.md

checkpoint-loads:
	@test -n "$(RUNS)" || { echo "checkpoint-loads: pass RUNS='<checkpoint-load.sh outdir> ...'" >&2; exit 2; }
	@$(PYTHON) tools/xemu/checkpoint-load.py $(addprefix --run ,$(RUNS)) -o inventory/checkpoint_loads.csv
	@$(PYTHON) tools/xemu/checkpoint-load.py --write-doc docs/systems/CHECKPOINTS.md

fmgr-dispatch:
	@$(PYTHON) tools/fmgr-dispatch.py --xbe "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" \
		-o inventory/fmgr_dispatch.csv
	@$(PYTHON) tools/fmgr-dispatch.py --write-doc docs/systems/CHECKPOINTS.md

fmgr-live-object:
	@$(PYTHON) tools/fmgr-live-object.py --xbe "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" \
		-o inventory/fmgr_singletons.csv
	@$(PYTHON) tools/fmgr-live-object.py --live "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" \
		--live-out inventory/fmgr_live_vtable.csv
	@echo "wrote inventory/fmgr_live_vtable.csv"
	@$(PYTHON) tools/fmgr-live-object.py --write-doc docs/systems/CHECKPOINTS.md

fmgr-installs:
	@$(PYTHON) tools/fmgr-installs.py --xbe "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" \
		-o inventory/fmgr_installs.csv
	@$(PYTHON) tools/fmgr-installs.py --write-doc docs/systems/CHECKPOINTS.md

linker-purge:
	@$(PYTHON) tools/linker-purge.py --xbe "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" \
		-o inventory/linker_purge.csv
	@$(PYTHON) tools/linker-purge.py --write-doc docs/systems/CHECKPOINTS.md

path-literals:
	@$(PYTHON) tools/path-literals.py --xbe "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" \
		-o inventory/path_literals.csv
	@$(PYTHON) tools/path-literals.py --write-doc docs/systems/CHECKPOINTS.md

save-path-compose:
	@$(PYTHON) tools/save-path-compose.py --xbe "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" \
		--dump "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15" -o inventory/save_path_compose.csv
	@$(PYTHON) tools/save-path-compose.py --write-doc docs/systems/CHECKPOINTS.md

hud-messages:
	@test -d "$(WORK)/UnrealScript" || { echo "hud-messages: run the extraction first (tools/pkg-script-text.py --all)" >&2; exit 2; }
	@$(PYTHON) tools/hud-messages.py --src "$(WORK)/UnrealScript"

# PLAN 7.3's other variable: which font the console's input line is drawn in. Reads the packages, so no boot.
hud-console-font:
	@test -d "$(SG1_DUMP)" || { echo "hud-console-font: SG1_DUMP not found: $(SG1_DUMP)"; exit 2; }
	@$(PYTHON) tools/hud-console-font.py "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15" \
		--text-height $(or $(TEXT_HEIGHT),30) -o inventory
	@echo "wrote inventory/hud_console_font.csv"

# PLAN 4.5's input: which functions belong to which module, and an address list per module for the exporter.
module-split:
	@$(PYTHON) tools/module-split.py --plan inventory/module_export_plan.csv
	@echo "wrote inventory/function_modules.csv and inventory/module_export_plan.csv"

# PLAN 4.5: one address list per module for `ExportAt.java @<file>`. Derived work, so it lands in $(WORK);
# the plan artifact prices the pass (an address list is a run of 1.04 functions, not a region).
module-lists:
	@$(PYTHON) tools/module-split.py --lists "$(WORK)/module-lists"

# PLAN 4.8: what the 6,063 exported bodies are -- their section, their direct call fan-in, whether a
# vtable holds them, and what their literals say. Reads the export `export-module.sh` leaves under
# $(WORK) plus the image, so it is not part of `make ci`; its `--check` is, and it needs no dump.
# PLAN 4.8: read one module's exported bodies -- the translation unit each asserts, its literals, its
# callee modules and its vtable slots. Needs the export but not the image; its `--check` is in `make ci`.
body-name:
	@test -d "$(WORK)/module-export/Engine" || { echo "no export under $(WORK)/module-export -- run the exporter first"; exit 2; }
	@$(PYTHON) tools/body-name.py --xbe "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" \
		-o inventory/body_names.csv --write-doc docs/NAMING.md --write-overrides

library-bodies:
	@test -f "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" || { \
		echo "no default.xbe under $(SG1_DUMP) -- export SG1_DUMP to the dump's parent directory"; exit 2; }
	@$(PYTHON) tools/library-bodies.py \
		"$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" \
		-o inventory/library_bodies.csv --write-doc docs/LIBRARIES.md

body-read:
	@test -d "$(WORK)/module-export/D3DDrv" || { echo "no export under $(WORK)/module-export -- run the exporter first"; exit 2; }
	@$(PYTHON) tools/body-read.py --module D3DDrv --root "$(WORK)/module-export" \
		-o inventory/d3ddrv_bodies.csv --write-doc docs/systems/RENDERER.md
	@$(PYTHON) tools/body-read.py --module XboxDrv --root "$(WORK)/module-export" \
		-o inventory/xboxdrv_bodies.csv --write-doc docs/systems/XBOX_DRIVER.md

class-ctors:
	@test -f "$(GAME_DIR)/default.xbe" || { echo "no default.xbe under $(GAME_DIR)"; exit 2; }
	@$(PYTHON) tools/class-ctors.py "$(GAME_DIR)/default.xbe" -o inventory --apply
	@$(PYTHON) tools/native-summary.py --symbols symbols/xbox_functions.csv --overrides symbols/xbe_overrides.csv -o inventory/
	@$(PYTHON) tools/make-symbols.py $(if $(SG1_DUMP),--xbe "$(GAME_DIR)/default.xbe") -o symbols
	@echo "wrote inventory/class_constructors.csv and the names it earned"

class-table-parents:
	@test -f "$(GAME_DIR)/default.xbe" || { echo "no default.xbe under $(GAME_DIR)"; exit 2; }
	@$(PYTHON) tools/class-table-parents.py "$(GAME_DIR)/default.xbe" --csv inventory/class_table_parents.csv
	@echo "wrote inventory/class_table_parents.csv"

module-census:
	@test -f "$(GAME_DIR)/default.xbe" || { echo "no default.xbe under $(GAME_DIR)"; exit 2; }
	@test -d "$(WORK)/module-export" || { echo "no export under $(WORK)/module-export -- run the exporter first (docs/MODULE_SPLIT.md)"; exit 2; }
	@$(PYTHON) tools/module-export-census.py --root "$(WORK)/module-export" --xbe "$(GAME_DIR)/default.xbe" \
		-o inventory/module_export_census.csv --write-doc docs/MODULE_CENSUS.md

# PLAN 4.5: which translation unit each unattributed function belongs to. Address locality, measured against
# held-out asserts at 89.9% (docs/MODULE_ATTRIBUTION.md). Reads the image only; no Ghidra, no boot. A
# `--holdout` run scores the rules and writes nothing: the artifact is the full-seed run.
module-attribution:
	@test -d "$(SG1_DUMP)" || { echo "SG1_DUMP not found: $(SG1_DUMP)"; exit 2; }
	@$(PYTHON) tools/module-attribution.py \
		--xbe "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" -o inventory
	@echo "wrote inventory/function_module_attribution.csv"

# PLAN 3.6: why the block locator misses, measured instead of guessed (the question docs/CLASS_DEFAULTS.md left open).
defaults-misses:
	@$(PYTHON) tools/class-defaults.py --misses "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/System" -o inventory
	@echo "wrote inventory/class_defaults_misses.csv"

# The values the defaults renderer refuses, reported as shape rows rather than values (PLAN 3.5, U18).
defaults-residue:
	@$(PYTHON) tools/defaults-residue.py "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/System" --csv inventory/defaults_residue.csv
	@echo "wrote inventory/defaults_residue.csv"

# A delegate's default is two compact indices -- an object reference and the bound function's name -- not a
# string (PLAN 3.5, U18). 39 of the dump's 42 values are the class binding a handler it declares to itself.
delegate-defaults:
	@$(PYTHON) tools/delegate-defaults.py "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/System" --csv inventory/delegate_defaults.csv
	@echo "wrote inventory/delegate_defaults.csv"

# Where each delegate tag sits in its block, and whether its value is a run of streamed record units rather than a
# value at all (PLAN 3.5, U18). Width separates the 39 that decode from the three residues.
defaults-blockstart:
	@$(PYTHON) tools/defaults-blockstart.py "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/System" --csv inventory/delegate_blockstart.csv
	@echo "wrote inventory/delegate_blockstart.csv"

# Every located block start, asked the same question the delegate sweep asked its three residues: does the first value
# tile as whole streamed record units, and does the same grammar started 1-4 bytes later tile just as often? A test
# only calibrated against a control can say a start sits inside the record array (PLAN 3.5, U18).
defaults-blockstart-census:
	@$(PYTHON) tools/defaults-blockstart.py "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/System" --census --census-csv inventory/defaults_blockstart_census.csv
	@echo "wrote inventory/defaults_blockstart_census.csv"

# The state bodies are not only in an extracted tree: each class ships its own `ScriptText`, and a `state` block
# is part of it. This reads the AI controllers' states, labels, overrides, gotos and sleeps straight out of the
# packages (PLAN 3.10, docs/systems/AI.md).
ai-states:
	@test -d "$(SG1_DUMP)" || { echo "SG1_DUMP not found: $(SG1_DUMP)"; exit 2; }; \
	$(PYTHON) tools/ai-states.py \
		--system "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/System" \
		-o inventory/ai_states.csv --write-doc docs/systems/AI.md

# A state's own statements and the handlers it overrides are different behaviour: the first is what the state
# runs, the second is code the engine calls while it is active. This walks a body as a statement tree and keeps
# kinds and callee names only -- never the code -- so the read is repeatable without publishing script (PLAN 3.10).
ai-state-body:
	@test -d "$(SG1_DUMP)" || { echo "SG1_DUMP not found: $(SG1_DUMP)"; exit 2; }; \
	$(PYTHON) tools/ai-state-body.py \
		--system "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/System" \
		-o inventory/ai_state_bodies.csv --write-doc docs/systems/AI.md

# The text is a floor on the source, not on the machine: a state whose body the text never declares is still a
# `UState` export, and its body is the `Function` exports its `Outer` names (PLAN 3.10, docs/systems/AI.md).
state-exports:
	@test -d "$(SG1_DUMP)" || { echo "SG1_DUMP not found: $(SG1_DUMP)"; exit 2; }; \
	$(PYTHON) tools/state-exports.py \
		--system "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/System" \
		-o inventory/state_exports.csv --write-doc docs/systems/AI.md

# A seat is a default property value, not behaviour, so this reads the emitter's tree rather than a boot: the
# two arrays are declared once on `StargateVehicle` and tile one tagged struct per slot.
vehicle-seats:
	@test -d "$(SG1_DUMP)" || { echo "SG1_DUMP not found: $(SG1_DUMP)"; exit 2; }; \
	$(PYTHON) tools/vehicle-seats.py \
		--system "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/System" \
		-o inventory/vehicle_seats.csv --write-doc docs/systems/VEHICLES.md

# A pawn's voice pack is a default property value, not a mission setting, so this reads the emitter's tree for
# every class under `StargatePawn` and joins it with the map actor table (docs/systems/AUDIO.md).
voice-packs:
	@test -d "$(SG1_DUMP)" || { echo "SG1_DUMP not found: $(SG1_DUMP)"; exit 2; }; \
	$(PYTHON) tools/voice-packs.py \
		--system "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/System" \
		-o inventory/voice_packs.csv --write-doc docs/systems/AUDIO.md

# A pack sets one default property, `VoiceSets`; this reads that array (the question `AUDIO.md` left open).
voice-sets:
	@test -d "$(SG1_DUMP)" || { echo "SG1_DUMP not found: $(SG1_DUMP)"; exit 2; }; \
	$(PYTHON) tools/voice-sets.py \
		--system "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/System" \
		-o inventory/voice_sets.csv --write-doc docs/systems/AUDIO.md

# A `VoiceSet` is an ordinary object, so its block starts at byte 0: `VDesc`, `VSounds`, `VSoundsDesc`. This
# reads the per-sound `VoiceDesc` elements (the question `AUDIO.md` still had open after `voice-sets`).
voice-descs:
	@test -d "$(SG1_DUMP)" || { echo "SG1_DUMP not found: $(SG1_DUMP)"; exit 2; }; \
	$(PYTHON) tools/voice-descs.py \
		--system "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/System" \
		-o inventory/voice_descs.csv --write-doc docs/systems/AUDIO.md

# A weapon names a bullet and the bullet names the shot: the reference graph the defaults block carries, read
# with the target classified by its own super chain (docs/systems/WEAPONS.md).
weapon-links:
	@test -d "$(SG1_DUMP)" || { echo "SG1_DUMP not found: $(SG1_DUMP)"; exit 2; }; \
	$(PYTHON) tools/weapon-links.py \
		--system "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/System" \
		-o inventory/weapon_links.csv --effective-out inventory/weapon_effective.csv \
		--write-doc docs/systems/WEAPONS.md

# A setting is not a value: this resolves each firing-chain hop through the super chain. It reads the
# settings table rather than the packages, so it needs no dump -- `make weapon-links` reads the dump.
weapon-effective:
	@$(PYTHON) tools/weapon-links.py --from-csv inventory/weapon_links.csv \
		--effective-out inventory/weapon_effective.csv --write-doc docs/systems/WEAPONS.md

# The opcodes above 0x5f call natives by slot; --native-calls reads every site against the caller's
# own declaration chain (docs/BYTECODE.md, "The opcodes above 0x5f are native calls").
native-opcodes:
	@test -d "$(SG1_DUMP)" || { echo "SG1_DUMP not found: $(SG1_DUMP)"; exit 2; }; \
	$(PYTHON) tools/uscript-dis.py --native-calls \
		"$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/System" -o inventory
	@echo "  class-table-parents  name a class table from the callers of the function that installs it"
	@echo "  class-table-residual  what a registration call pushes, and what slots 19/20 hold (needs the dump)"
	@echo "  vcall-dispatch  every .text site that dispatches slots 19/20 (needs the dump)"
	@echo "  vcall-field-owner  which field holds the object at each of those sites (needs the dump)"
	@echo "  field-objects  which class holds a live object at each of those offsets (needs a boot and PORT)"
	@echo "  live-class-tables  table_va,class,objects from the guest objects at the title (needs a boot and PORT)"
	@echo "  live-class-tables-m00  the same read with M00 loaded (needs a boot and PORT)"
	@echo "  class-scripttext-ref  the class record third field, resolved as a ScriptText export reference"
	@echo "  native-vtable  how long a native vtable really is, which rule carries it, who mentions it and what it shares (dump)"

# U8's two residuals (docs/NATIVE_CLASS_TREE.md): every registration call's argument list, and what slots 19/20
# hold in all 492 class tables. Needs the image; both artifacts are addresses and counts, no game data.
class-table-residual:
	@test -f "$(GAME_DIR)/default.xbe" || { echo "no default.xbe under $(GAME_DIR)"; exit 2; }
	@$(PYTHON) tools/class-table-residual.py sites --xbe "$(GAME_DIR)/default.xbe" -o inventory/registration_arguments.csv
	@$(PYTHON) tools/class-table-residual.py slots --xbe "$(GAME_DIR)/default.xbe" -o inventory/class_table_slot_pairs.csv
	@echo "wrote inventory/registration_arguments.csv and inventory/class_table_slot_pairs.csv"

# U8's third residual (docs/NATIVE_CLASS_TREE.md): which `.text` sites dispatch the two virtuals the Actor tables
# install. Pure decode of the image; the artifact is addresses and counts, no game data.
vcall-dispatch:
	@test -f "$(GAME_DIR)/default.xbe" || { echo "no default.xbe under $(GAME_DIR)"; exit 2; }
	@$(PYTHON) tools/vcall-dispatch.py --xbe "$(GAME_DIR)/default.xbe" -o inventory/vcall_dispatch_sites.csv
	@echo "wrote inventory/vcall_dispatch_sites.csv"

# U8's field half: which member load hands the pair its object, and which static route can name that
# object's class (docs/NATIVE_CLASS_TREE.md). Decode of the image only; the artifact is offsets and counts.
vcall-field-owner:
	@test -f "$(GAME_DIR)/default.xbe" || { echo "no default.xbe under $(GAME_DIR)"; exit 2; }
	@$(PYTHON) tools/vcall-field-owner.py --xbe "$(GAME_DIR)/default.xbe" -o inventory/vcall_field_owners.csv
	@echo "wrote inventory/vcall_field_owners.csv"

# The +0x220 helper's table (docs/NATIVE_CLASS_TREE.md, U8): a run of code pointers can span two
# classes, and only the install store shows the split. Pure decode of the image; the artifact is
# addresses and counts, no game data.
native-vtable:
	@test -f "$(GAME_DIR)/default.xbe" || { echo "no default.xbe under $(GAME_DIR)"; exit 2; }
	@$(PYTHON) tools/native-vtable.py "$(GAME_DIR)/default.xbe" --block -o inventory/native_vtable_merges.csv
	@echo "wrote inventory/native_vtable_merges.csv"
	@$(PYTHON) tools/native-vtable.py "$(GAME_DIR)/default.xbe" --reach -o inventory/native_vtable_reach.csv
	@echo "wrote inventory/native_vtable_reach.csv"
	@$(PYTHON) tools/native-vtable.py "$(GAME_DIR)/default.xbe" --share -o inventory/native_vtable_shared.csv
	@echo "wrote inventory/native_vtable_shared.csv"

# U8's layout half: which class holds a live object at each of the six field offsets, read from a booted guest
# (docs/NATIVE_CLASS_TREE.md). It needs a run and that run's GDB stub, so it is not part of `make ci`.
field-objects:
	@test -n "$(PORT)" || { echo "set PORT to the guest's GDB stub (tools/xemu/drive.py --gdb=PORT --linger=SECS)"; exit 2; }
	@test -f "$(GAME_DIR)/default.xbe" || { echo "no default.xbe under $(GAME_DIR)"; exit 2; }
	@$(PYTHON) tools/xemu/field-objects.py --port $(PORT) --xbe "$(GAME_DIR)/default.xbe" \
		--from-csv inventory/vcall_field_owners.csv --csv inventory/field_object_owners.csv
	@echo "wrote inventory/field_object_owners.csv"

# U8's naming half, and the read later live tools quote: `table_va,class,objects` from the guest's own objects,
# taken at the title screen (`live-class-tables`) and with M00 loaded (`live-class-tables-m00`, the larger world
# that scored the same verdicts). Both are boot-side reads -- `tools/xemu/class-tables-live.py` needs a running
# guest with a GDB stub -- so neither target is part of `make ci`.
live-class-tables:
	@test -n "$(PORT)" || { echo "set PORT to the guest's GDB stub (tools/xemu/drive.py --gdb=PORT --linger=SECS)"; exit 2; }
	@test -f "$(GAME_DIR)/default.xbe" || { echo "no default.xbe under $(GAME_DIR)"; exit 2; }
	@$(PYTHON) tools/xemu/class-tables-live.py --port $(PORT) --xbe "$(GAME_DIR)/default.xbe" \
		--csv inventory/live_class_tables.csv
	@echo "wrote inventory/live_class_tables.csv"

live-class-tables-m00:
	@test -n "$(PORT)" || { echo "set PORT to the guest's GDB stub (tools/xemu/drive.py --gdb=PORT --linger=SECS)"; exit 2; }
	@test -f "$(GAME_DIR)/default.xbe" || { echo "no default.xbe under $(GAME_DIR)"; exit 2; }
	@$(PYTHON) tools/xemu/class-tables-live.py --port $(PORT) --xbe "$(GAME_DIR)/default.xbe" \
		--csv inventory/live_class_tables_m00.csv
	@echo "wrote inventory/live_class_tables_m00.csv"

# `UStruct::Serialize`'s third field, resolved through the archive's object-reference method rather than by width
# (docs/NATIVE_VM.md, *The prefix's third field is a ScriptText export*). Reads the packages; no boot.
class-scripttext-ref:
	python3 tools/class-scripttext-ref.py --dump $(SG1_DUMP) --sweep -o inventory
	@python3 tools/class-scripttext-ref.py --report

# The array head the writer's rule finds (after sixteen zero bytes) as the bound on the located block start
# (docs/NATIVE_VM.md, *The array's head sits after the sixteen zero bytes*). Reads the packages; no boot.
class-record-bound:
	python3 tools/class-record-bound.py --dump $(SG1_DUMP) --sweep -o inventory
	@python3 tools/class-record-bound.py --report

class-record-room:
	python3 tools/class-record-room.py --dump $(SG1_DUMP) --sweep -o inventory
	@python3 tools/class-record-room.py --report

class-tail-units:
	python3 tools/class-tail-units.py --dump $(SG1_DUMP) --sweep -o inventory
	@python3 tools/class-tail-units.py --report
	@python3 tools/class-tail-units.py --dump $(SG1_DUMP) --join

class-tail-run:
	python3 tools/class-tail-run.py --dump $(SG1_DUMP) --sweep -o inventory
	@python3 tools/class-tail-run.py --report

class-record-overshoot:
	python3 tools/class-record-overshoot.py --sweep -o inventory
	@python3 tools/class-record-overshoot.py --report

# The defaults stream read from the located block start: the one witness left on the rows whose array
# overshoots it and whose head census is silent (`docs/NATIVE_VM.md`, *The defaults stream closes on the
# block start*). The third mode reads the same stream at every offset inside a short row's trailer, which is
# how the three rows that stop 41-59 bytes short were settled. Needs the dump; `--check` is synthetic.
class-overshoot-stream:
	python3 tools/class-overshoot-stream.py --dump $(SG1_DUMP) --sweep -o inventory
	python3 tools/class-overshoot-stream.py --dump $(SG1_DUMP) --corpus -o inventory
	python3 tools/class-overshoot-stream.py --dump $(SG1_DUMP) --trailer 59 -o inventory
	@python3 tools/class-overshoot-stream.py --report
	@python3 tools/class-overshoot-stream.py --report-corpus
	@python3 tools/class-overshoot-stream.py --report-trailer

class-tail:
	python3 tools/class-tail.py $(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/System --sweep -o inventory

# The class record's prefix: `UStruct::Serialize`'s field order applied to the stream, scored against the
# package's own export table (`docs/NATIVE_VM.md`, *The class record's prefix*).
class-record-fields:
	python3 tools/class-record-fields.py --dump $(SG1_DUMP) --sweep -o inventory
	@python3 tools/class-record-fields.py --report

class-record-middle:
	python3 tools/class-record-middle.py --dump $(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15 --sweep -o inventory
	@python3 tools/class-record-middle.py --report

# Whether the stride-9 record array has a head: what ends in front of its run, against the chance rate of the same
# test at every other byte of the same window (`docs/NATIVE_VM.md`, *The run has no head*).
class-record-run:
	python3 tools/class-record-run.py --dump $(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15 --sweep -o inventory
	@python3 tools/class-record-run.py --report

# Whether the stride-9 record array's body continues behind the run, and whether a count sits at the end of
# it: the walk at the writer's own element widths, against the same walk aimed one byte off
# (`docs/NATIVE_VM.md`, *The walk back continues the record's own widths*).
class-record-walkback:
	python3 tools/class-record-walkback.py --dump $(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15 --sweep -o inventory
	@python3 tools/class-record-walkback.py --report

# Where the record's 12-byte-record array sits and what follows it: the head at the position `UClass::Serialize`
# gives it, the trailing name arrays the version gates, and the residue before the defaults
# (`docs/NATIVE_VM.md`, *The array's count is its own first field*).
class-record-array:
	python3 tools/class-record-array.py --dump $(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15 --sweep -o inventory
	@python3 tools/class-record-array.py --report

class-record-array-head:
	python3 tools/class-record-array-head.py --dump $(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15 --sweep -o inventory
	@python3 tools/class-record-array-head.py --report

class-record-array-search:
	python3 tools/class-record-array-search.py --dump $(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15 --sweep -o inventory
	@python3 tools/class-record-array-search.py --report

class-record-array-blind:
	python3 tools/class-record-array-blind.py --dump $(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15 --sweep -o inventory
	@python3 tools/class-record-array-blind.py --report

class-record-element-fields:
	python3 tools/class-record-element-fields.py --dump $(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15 --sweep -o inventory
	@python3 tools/class-record-element-fields.py --report

class-record-element:
	python3 tools/class-record-element.py --dump $(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15 --sweep -o inventory
	@python3 tools/class-record-element.py --report

class-member-map:
	python3 tools/class-member-map.py --xbe $(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe -o inventory
	python3 tools/class-member-map.py --xbe $(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe --targets -o inventory
	@python3 tools/class-member-map.py --report

member-access-census:
	python3 tools/member-access-census.py --xbe $(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe \
		--disp 0x4b8 --disp 0x4c4 --disp 0x4d0 --disp 0x4e8 --disp 0x4f4 --disp 0x500 --disp 0x514 -o inventory
	python3 tools/member-access-census.py --xbe $(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe \
		--disp 0x4b8 --disp 0x4c4 --disp 0x4d0 --disp 0x4e8 --disp 0x4f4 --disp 0x500 --disp 0x514 --report

class-array-head:
	python3 tools/member-access-census.py --xbe $(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe \
		--disp 0x4c4 --disp 0x4c8 --disp 0x4cc -o inventory --artifact class_array_head_census.csv
	@python3 tools/member-access-census.py --xbe $(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe \
		--disp 0x4c4 --disp 0x4c8 --disp 0x4cc --report

class-config-pair:
	python3 tools/member-access-census.py --xbe $(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe \
		--disp 0x4b0 --disp 0x4b4 -o inventory --artifact class_config_pair_census.csv
	@python3 tools/member-access-census.py --xbe $(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe \
		--disp 0x4b0 --disp 0x4b4 --report

member-assert-names:
	python3 tools/member-assert-names.py --xbe $(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe -o inventory
	@python3 tools/member-assert-names.py --xbe $(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe --report

class-record-tail:
	python3 tools/class-record-tail.py --dump $(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15 --sweep -o inventory
	@python3 tools/class-record-tail.py --report

class-record-gap:
	python3 tools/class-record-gap.py --dump $(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15 --sweep -o inventory
	@python3 tools/class-record-gap.py --report

class-record-order:
	python3 tools/class-record-order.py --dump $(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15 --sweep -o inventory
	@python3 tools/class-record-order.py --report

class-record-gap-front:
	python3 tools/class-record-order.py --dump $(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15 --gap-sweep -o inventory
	@python3 tools/class-record-order.py --gap-report

class-record-open-front:
	python3 tools/class-record-open-front.py --dump $(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15 --sweep -o inventory
	@python3 tools/class-record-open-front.py --report

class-record-open-tail:
	python3 tools/class-record-open-tail.py --dump $(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15 --sweep -o inventory
	@python3 tools/class-record-open-tail.py --report

class-record-no-shape:
	python3 tools/class-record-no-shape.py --dump $(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15 --sweep -o inventory
	@python3 tools/class-record-no-shape.py --report

class-blockstart-guard:
	python3 tools/class-blockstart-guard.py --dump $(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15 --sweep -o inventory
	@python3 tools/class-blockstart-guard.py --report

class-group-stop:
	python3 tools/class-group-stop.py --dump $(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15 --sweep -o inventory
	@python3 tools/class-group-stop.py --report

class-group-after-scan:
	python3 tools/class-group-after-scan.py --dump $(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15 --sweep -o inventory
	@python3 tools/class-group-after-scan.py --report

class-defaults-moved:
	python3 tools/class-defaults-moved.py "$(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/System" --sweep -o inventory
	@python3 tools/class-defaults-moved.py --report

class-defaults-silent:
	python3 tools/class-defaults-silent.py --dump $(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15 --sweep -o inventory
	@python3 tools/class-defaults-silent.py --report

class-defaults-single:
	python3 tools/class-defaults-silent.py --dump $(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15 --gained -o inventory   # writes inventory/class_defaults_single.csv
	@python3 tools/class-defaults-silent.py --report-gained
class-record-front:
	python3 tools/class-record-front.py --dump $(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15 --sweep -o inventory
	@python3 tools/class-record-front.py --report

archive-slots:
	python3 tools/archive-slots.py --image $(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe -o inventory
	@python3 tools/archive-slots.py --report

record-writer-calls:
	python3 tools/record-writer-calls.py --image $(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe -o inventory
	@python3 tools/record-writer-calls.py --report

class-prefix:
	python3 tools/class-prefix.py $(SG1_DUMP)/Stargate_SG-1_-_The_Alliance-2005-12-15/System --sweep -o inventory
	@python3 tools/class-prefix.py --report -o inventory
