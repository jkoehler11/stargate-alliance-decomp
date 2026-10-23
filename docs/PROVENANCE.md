# Provenance of the target build

Everything downstream assumes the bytes described here. If any value below changes, every
inventory number in this repo is suspect.

## Identity

| Field | Value |
|---|---|
| Title | *Stargate SG-1: The Alliance* |
| Platform | Xbox (original) |
| Build date | 2005-12-15 |
| Origin | Xbox Debug Kit dump |
| Released by | CVLT OF OSIRIS, 2019-05-06 |
| Public listing | Hidden Palace, `Stargate SG-1: The Alliance (Dec 15, 2005 prototype)` |
| Archive | `Stargate_SG-1_-_The_Alliance-2005-12-15.7z` |
| Archive size | 1,015,500,550 bytes |
| Archive SHA-256 | `d70e15d5ceb479bb8443f2c0bcf2d8884f913a02033d7d8a8e33f80e622d47f4` |

Verified locally on 2026-10-22 by hashing the downloaded archive; re-verified before the manifest
was generated.

## Extracted tree

| Field | Value |
|---|---|
| Extraction tool | `py7zr` 1.1.4 (no `7z` in the container) |
| Entries | 4,082 (4,006 files, 76 directories) |
| Size | 2,253,398,443 bytes (2.10 GiB) |
| Extensions | 29 distinct |
| Manifest | `inventory/manifest.sha256` — SHA-256 + relative path for all 4,006 files |

Root layout: `README.txt` (release notes) + `Stargate_SG-1_-_The_Alliance-2005-12-15/` containing
`System/`, `Maps/`, `Textures/`, `StaticMeshes/`, `Animations/`, `Sounds/`, `Speech/`, `Music/`,
`Resources/`, `Help/`, plus `default.xbe`, `splash.raw`, `xbox.bin`.

Content counts: 73 `.unr` maps, 18 `.u` packages, 3,287 `.wav`, 170 `.utx`, 91 `.usx`, 83 `.ukx`,
55 `.uax`, 9 `.sgs` checkpoints, 3 `.sgp` profiles, 3 `.ka` + 1 `.kaw` ragdolls, 8 `.lad` lip-sync
controllers. Full tables in `inventory/extensions.csv` and `inventory/directories.csv`.

File timestamps in the tree range 2019-2023 and reflect when the debug kit's HDD was copied, not the
build date; they carry no information and are not part of the manifest.

## Build fingerprint

- `default.xbe`: 6,209,536 bytes, image base `0x00010000`, entry `0x00010184`, 15 sections
  (`.text` 0x11000–0x4180FC, `.rdata`, `.data`, `XNET`, `XONLINE`, `D3D`, `D3DX`, `XGRPH`, `DSOUND`,
  `XPP`, `XON_RD`, `DOLBY`, `$$XTIMAGE`, `$$XSIMAGE`). No `RSDS`/PDB record: symbols were stripped,
  so native work starts from asserts and signature libraries, not from debug info.
- Packages: 18 `.u`, package version **130** (licensee **4**) except `UPreview.u` at 128.
  UELib identifies this `(version, licensee)` pair as `SG1_TA` and it is the only build profile
  UELib ships for this game — confirmed by loading `Core.u`, which reports `Build:SG1_TA`.
- Assert strings embed the studio's tree: `\alienbrainWork\Stargate-sg1\Game\GameXBOX\<Module>\Src\<File>.cpp`,
  154 translation units across 12 modules (see `inventory/xbe_modules.csv`).
- Script compiler was itself an Xbox executable (`D:\uc.xbe`), and `Pawn.ucpp`-style artefacts show
  Perception compiled hot script classes to C++ — recorded as a known trap in `PLAN.md` §4.4.

## How to reproduce

```sh
# 1. obtain the release archive yourself (not redistributable; not in this repo)
sha256sum Stargate_SG-1_-_The_Alliance-2005-12-15.7z     # must equal the hash above
# 2. extract to real disk (a container /tmp is often a 512 MB tmpfs)
python3 -c "import py7zr; py7zr.SevenZipFile('...7z').extractall('/path/with/space')"
# 3. verify the tree
make verify-dump SG1_DUMP=/path/with/space
```

`make verify-dump` fails with a per-file list on any difference: unlisted file, changed hash, or
missing file. A dump that passes is byte-identical to the one this project's numbers were measured
on.

## What is not in this repository

No part of the dump. `README.txt`, `System/*.u`, maps, assets, `default.xbe` and `Packages.md5` are
all excluded by `.gitignore` and by policy (`docs/LEGAL.md`). The manifest lists hashes and paths,
never content; the inventories list structural counts, names and addresses. Anyone can verify that
their dump is the right one without this repo ever carrying a byte of it.

## Cross-references

- Hidden Palace listing (release notes, dump method, other preserved builds of the title).
- `README.txt` in the dump for the release group's playability notes (single-player campaign is
  entered through **Continue**, and the network must be disconnected or an `unreal://` overlay
  appears — both matter for the boot harness in Phase 7).
