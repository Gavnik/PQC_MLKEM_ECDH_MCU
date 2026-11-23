# PQC_MLKEM_ECDH_MCU

Firmware, scripts and measurement data for an empirical comparison of **ML-KEM** (FIPS 203) and **ECDH** (X25519, P-256, P-384, P-521) on four microcontroller CPU cores, with **write XOR execute (W^X)** memory protection enforced and the cryptographic code placed either in flash or in SRAM.

| MCU | CPU core | ISA | Clock | Memory protection |
|---|---|---|---|---|
| RP2350 (Raspberry Pi Pico 2 W) | Cortex-M33 | Armv8-M | 150 MHz | MPU |
| RP2350 (Raspberry Pi Pico 2 W) | Hazard3 | RV32 | 150 MHz | PMP (our locked map) |
| PSoC 62 (CY8CPROTO-062-4343W) | Cortex-M4F | Armv7E-M | 100 MHz | MPU |
| ESP32-S3 (ESP32-S3-DevKitC N16R8) | Xtensa LX7 | Xtensa | 240 MHz | PMS (our locked map) |

Software: Zephyr `v4.4.0-10003-gac620cbd62f8` and wolfSSL `5.9.2`, the exact versions every measured image reports on boot (see Prerequisites).

The RP2350 images are built for Zephyr's `rpi_pico2` target (not the `/w` variant), so the board's Wi-Fi chip is not used; a plain Pico 2 runs the same images.

Only the cryptographic operations are measured: ML-KEM KeyGen, Encaps and Decaps, and ECDH key generation and shared-secret derivation. Every image is built without networking (`CONFIG_NETWORKING=n`), and the TLS 1.3 client of a later study is not included.

## Repository layout

```
firmware/                 Zephyr application (one source tree for all 15 images)
  prj.conf, Kconfig       common configuration and our Kconfig options
  boards/                 per-board configuration and devicetree overlays
  config/                 overlays: placement, assembly on/off, MPU, ESP32 options
  cmake/                  hybrid relocation, -O2/-Os split, RP2350 flash workaround
  src/                    benchmark firmware (main.c), timing core, wolfSSL user_settings.h
  tools/                  Zephyr/ESP32 tree patches and the build-time placement check
  patches/                one-line fixes to the Zephyr and wolfSSL trees (see One-time patches)
scripts/                  host-side drivers (Python, talk to the board over its serial console)
  pqc_sweep.py            timing and memory sweep -> CSV (+ raw samples)
  fault_check.py          W^X fault-injection tests -> CSV
  code_identity_check.py  confirms the crypto code is identical in a protected / unprotected pair
  memtopo_check.py        PSoC 62 memory-topology diagnostic -> verdict
  console_cmd.py          sends console commands to a board and saves the output
  kem_seed_fingerprints.txt  ML-KEM key fingerprints from an independent FIPS 203 implementation (kyber-py)
  figures/                scripts that regenerate the paper figures from data/summary
data/
  timing/                 measured dataset (15 images; per operation 3 rounds, each of 3 warm-up and 100 timed runs)
  wx_off_control/         the same sweep on images built with W^X disabled (control experiment)
  fault_tests/            70 W^X fault-injection results
  protection_maps/        live MPU / PMP / PMS configuration of every image
  psoc62_memory_topology/ raw output of the PSoC 62 memory-topology diagnostic
  summary/                workbooks with every table of the paper (formulas over the CSVs)
figures/                  the figures as they appear in the paper (PDF)
```

## Prerequisites

A Linux host (tested on Debian-based Parrot OS, x86_64). The workspace must be `~/zephyrproject`, because `firmware/CMakeLists.txt` loads wolfSSL from `~/zephyrproject/modules/crypto/wolfssl`.

### 1. System packages

```bash
sudo apt update
sudo apt install --no-install-recommends -y \
  git cmake ninja-build gperf ccache dfu-util device-tree-compiler wget \
  python3-dev python3-venv python3-pip python3-setuptools python3-tk python3-wheel \
  xz-utils file make gcc gcc-multilib g++-multilib libsdl2-dev libmagic1
cmake --version            # must be 3.20.5 or newer
```

### 2. Python environment and west

```bash
python3 -m venv ~/zephyrproject/.venv
source ~/zephyrproject/.venv/bin/activate      # in every new terminal
pip install west
```

### 3. Zephyr at the measured commit

```bash
west init ~/zephyrproject
cd ~/zephyrproject/zephyr && git checkout ac620cbd62f80bc8698337c051982d71a449a062
cd ~/zephyrproject && west update
pip install -r ~/zephyrproject/zephyr/scripts/requirements.txt   # before zephyr-export
west zephyr-export
west packages pip --install                                      # e.g. esptool for the ESP32-S3
west blobs fetch hal_espressif
```

### 4. wolfSSL 5.9.2 as a Zephyr module

```bash
git clone --depth 1 --branch v5.9.2-stable https://github.com/wolfSSL/wolfssl \
    ~/zephyrproject/modules/crypto/wolfssl
grep LIBWOLFSSL_VERSION_STRING ~/zephyrproject/modules/crypto/wolfssl/wolfssl/version.h   # "5.9.2"
```

### 5. Zephyr SDK 1.0.1 (Arm, RISC-V and Xtensa toolchains)

This Zephyr revision needs SDK 1.0 or newer.

```bash
cd ~
wget https://github.com/zephyrproject-rtos/sdk-ng/releases/download/v1.0.1/zephyr-sdk-1.0.1_linux-x86_64_gnu.tar.xz
tar xf zephyr-sdk-1.0.1_linux-x86_64_gnu.tar.xz
cd zephyr-sdk-1.0.1 && ./setup.sh -t all -h -c
sudo cp ~/zephyr-sdk-1.0.1/hosttools/sysroots/x86_64-pokysdk-linux/usr/share/openocd/contrib/60-openocd.rules /etc/udev/rules.d
sudo udevadm control --reload && sudo udevadm trigger
```

### 6. This repository, flashing tools and host scripts

```bash
cd ~/zephyrproject
git clone <this repository> PQC_MLKEM_ECDH_MCU
APP=~/zephyrproject/PQC_MLKEM_ECDH_MCU/firmware
pip install -r PQC_MLKEM_ECDH_MCU/requirements.txt
```

You also need `picotool` for the RP2350 and Infineon's OpenOCD build for the PSoC 62 (KitProg3). The ESP32-S3 flashes with `west flash`.

## One-time patches

These changes to the Zephyr and wolfSSL trees are needed for the hybrid placement and its W^X layout. Run them again after every `west update`. The paths below assume the workspace from Prerequisites.

1. Zephyr tree: MPU region for the relocated tables, MPU alignment, RISC-V `.srodata` and PMP tiles. Each change is marked, and backups are kept as `*.pqc-orig`:

```bash
python3 $APP/tools/zephyr_tables_ro_patch.py --zephyr ~/zephyrproject/zephyr
python3 $APP/tools/zephyr_tables_ro_patch.py --zephyr ~/zephyrproject/zephyr --check
```

2. wolfSSL, for the ESP32-S3 "fast" X25519 build only. This lets `-DWOLFSSL_XTENSA_FAST_C25519` lift the line in `settings.h` that forces `CURVE25519_SMALL` on every Xtensa target:

```bash
cd ~/zephyrproject/modules/crypto/wolfssl && patch -p1 < $APP/patches/wolfssl-5.9.2-xtensa-fast-c25519-guard.patch
```

   Without this patch, `config/overlay-x25519fast-esp32.conf` has no effect, and the firmware reports `c25519=small`.

3. Zephyr, RP2350 boot block (applied to all RP2350 images, both cores). The patch aligns the image-definition block to 4 bytes. Without it, the Hazard3 image's block is not word-aligned, the boot ROM ignores it, and the board stays in BOOTSEL. On the Cortex-M33 the block is already aligned, so the patch changes nothing there:

```bash
cd ~/zephyrproject/zephyr && patch -p1 < $APP/patches/zephyr-rp2350-rom_start-align4.patch
```

4. ESP32-S3 linker script, after the first ESP32-S3 build (`build-lx7` below). This adds the guarded hybrid blocks to `soc/espressif/esp32s3/default.ld`:

```bash
python3 $APP/tools/esp32_iram_patch.py --build build-lx7 \
        --ld ~/zephyrproject/zephyr/soc/espressif/esp32s3/default.ld
```

## Build the 15 images

```bash
b(){ west build -p -b $2 $APP -d $1 -- -DEXTRA_CONF_FILE="$3" $4; }
M33=rpi_pico2/rp2350a/m33;  HZ3=rpi_pico2/rp2350a/hazard3
M4=cy8cproto_062_4343w/cy8c624abzi_s2d44;  LX7=esp32s3_devkitc/esp32s3/procpu
L33=boards/rpi_pico2_rp2350a_m33_l1only.conf;  NF=config/overlay-rp2350-noflashcmd.conf
LM4="config/overlay-l1only.conf;boards/cy8cproto_062_4343w.conf";  LX=boards/esp32s3_devkitc_procpu_l1only.conf

# flash placement
b build-m33-on   $M33 "$L33;config/overlay-mpu-on.conf;$NF"
b build-m33-off  $M33 "$L33;config/overlay-asmoff.conf;config/overlay-mpu-on.conf;$NF"
b build-hz3      $HZ3 "boards/rpi_pico2_rp2350a_hazard3.conf;$NF"
b build-m4-on    $M4  "$LM4"
b build-m4-off   $M4  "$LM4;config/overlay-asmoff.conf"
b build-lx7      $LX7 "$LX"
b build-lx7-fast $LX7 "$LX;config/overlay-x25519fast-esp32.conf"

# hybrid placement (crypto code and tables in SRAM)
b build-hyb-m33-on   $M33 "$L33;config/overlay-hyb-arm.conf;$NF"
b build-hyb-m33-off  $M33 "$L33;config/overlay-asmoff.conf;config/overlay-hyb-arm.conf;$NF"
b build-hyb-hz3      $HZ3 "boards/rpi_pico2_rp2350a_hazard3.conf;config/overlay-hyb-rv.conf;$NF"
b build-hyb-m4-on    $M4  "$LM4;config/overlay-hyb-arm.conf"
b build-hyb-m4-off   $M4  "$LM4;config/overlay-asmoff.conf;config/overlay-hyb-arm.conf"
b build-hyb-lx7      $LX7 "$LX;config/overlay-iram-esp32.conf"
b build-hyb-lx7-fast $LX7 "$LX;config/overlay-iram-esp32.conf;config/overlay-x25519fast-esp32.conf"

# PSoC 62 only: tables-only placement (code stays in flash, tables in SRAM)
b build-hyb-m4-tables-on $M4 "$LM4;config/overlay-hyb-arm.conf" -DPQC_HYB_PART=tables
```

`-on` / `-off` is the wolfSSL Arm assembly. On the PSoC 62, `boards/cy8cproto_062_4343w_cy8c624abzi_s2d44.overlay` is applied automatically. It starts the Cortex-M4 RAM at `0x08002000`, clear of the Cortex-M0+ sleep image. `lx7` is wolfSSL's stock Xtensa X25519 (`CURVE25519_SMALL`), and `lx7-fast` is its 32-bit-limb X25519, which needs patch 2 above. wolfSSL does not vouch for the constant-time behaviour of the fast X25519 on Xtensa, so treat it as a performance reference only.

### Build-time checks

```bash
python3 $APP/tools/placement_check.py build-* | grep "==\|VERDICT"
```

Expected verdicts:
- Flash images: `FLASH build`.
- Hybrid images: `HYBRID OK: crypto code in SRAM, no crypto constant left in flash`.
- `build-hyb-m4-tables-on`: `TABLES-ONLY HYBRID OK`.

wolfSSL and `main.c` must be compiled at `-O2` and Zephyr at `-Os`. Each image reports this as `opt=split-O2` on its ID line.

## Flash

```bash
# RP2350 (hold BOOTSEL, or use picotool -f on a running image)
picotool load -f build-m33-on/zephyr/zephyr.uf2 && picotool reboot -f
# PSoC 62 (KitProg3), with Infineon's OpenOCD
west flash -d build-m4-on --openocd <ifx-openocd>/bin/openocd --openocd-search <ifx-openocd>/scripts
# ESP32-S3
west flash -d build-lx7
```

Use the `/dev/serial/by-id/...` name of each board as `<port>` below.

## Run

On every image, first prove W^X, then measure:

```bash
python3 scripts/fault_check.py --port <port> --out faults.csv      # must end with ALL PASS
python3 scripts/pqc_sweep.py   --port <port> --out timing.csv      # 3 rounds, each of 3 warm-up + 100 timed runs
```

`pqc_sweep.py` refuses an image whose ID line does not report live W^X (`wx=mpu`, `wx=pmp` or `wx=pms`) and `opt=split-O2`. Each ML-KEM round's key fingerprint is checked against `scripts/kem_seed_fingerprints.txt`, which was produced from the same seeds by an independent FIPS 203 implementation (kyber-py). Every row of the CSV records the image's self-report (board, placement, assembly, X25519 variant, W^X mechanism). The raw per-iteration samples go to `<out>.raw`.

The fault tests depend on the image:
- **Flash images:** `faultw` (write to code) and `faultx` (execute data).
- **Hybrid images:** additionally `faultt` (write to tables), `faulttx` (execute tables) and the last-word variants `faultwend`, `faulttend`, `faulttxend`.

### W^X-off control

Each image was also built with its protection disabled and measured with:

```bash
python3 scripts/pqc_sweep.py --port <port> --out timing_wxoff.csv --control-wx-off
python3 scripts/code_identity_check.py build-hyb-m33-on build-hyb-m33-on-wxoff   # same crypto instructions?
```

Each twin is its measured image with the protection switched off and nothing else changed:

- **Hazard3 and ESP32-S3:** the same command plus `-DCONFIG_PQC_WX=n`. The protection on these cores is our own boot-time code, so this leaves it out.
- **Cortex-M33, flash images:** the same command without `config/overlay-mpu-on.conf`. Zephyr leaves the MPU off on this board, so the overlay is what turns it on.
- **Cortex-M33 hybrid images and all Cortex-M4F images:** the same command plus `-DCONFIG_ARM_MPU=n`. Here the MPU comes from `overlay-hyb-arm.conf` (Cortex-M33 hybrid) or is on by default (PSoC 62).

```bash
b build-m33-on-wxoff       $M33 "$L33;$NF"
b build-m33-off-wxoff      $M33 "$L33;config/overlay-asmoff.conf;$NF"
b build-hz3-wxoff          $HZ3 "boards/rpi_pico2_rp2350a_hazard3.conf;$NF" -DCONFIG_PQC_WX=n
b build-m4-on-wxoff        $M4  "$LM4" -DCONFIG_ARM_MPU=n
b build-m4-off-wxoff       $M4  "$LM4;config/overlay-asmoff.conf" -DCONFIG_ARM_MPU=n
b build-lx7-wxoff          $LX7 "$LX" -DCONFIG_PQC_WX=n
b build-lx7-fast-wxoff     $LX7 "$LX;config/overlay-x25519fast-esp32.conf" -DCONFIG_PQC_WX=n
b build-hyb-m33-on-wxoff   $M33 "$L33;config/overlay-hyb-arm.conf;$NF" -DCONFIG_ARM_MPU=n
b build-hyb-m33-off-wxoff  $M33 "$L33;config/overlay-asmoff.conf;config/overlay-hyb-arm.conf;$NF" -DCONFIG_ARM_MPU=n
b build-hyb-hz3-wxoff      $HZ3 "boards/rpi_pico2_rp2350a_hazard3.conf;config/overlay-hyb-rv.conf;$NF" -DCONFIG_PQC_WX=n
b build-hyb-m4-on-wxoff    $M4  "$LM4;config/overlay-hyb-arm.conf" -DCONFIG_ARM_MPU=n
b build-hyb-m4-off-wxoff   $M4  "$LM4;config/overlay-asmoff.conf;config/overlay-hyb-arm.conf" -DCONFIG_ARM_MPU=n
b build-hyb-lx7-wxoff      $LX7 "$LX;config/overlay-iram-esp32.conf" -DCONFIG_PQC_WX=n
b build-hyb-lx7-fast-wxoff $LX7 "$LX;config/overlay-iram-esp32.conf;config/overlay-x25519fast-esp32.conf" -DCONFIG_PQC_WX=n
b build-hyb-m4-tables-on-wxoff $M4 "$LM4;config/overlay-hyb-arm.conf" "-DPQC_HYB_PART=tables -DCONFIG_ARM_MPU=n"
```

Without the MPU, Zephyr's hardware stack protection is off as well, and the relocated blocks are aligned to 4 bytes instead of the MPU's 32, so the twins' memory layout differs slightly. `code_identity_check.py` confirms that the wolfSSL instructions are nevertheless identical in each pair; each twin's `zephyr/.config` records the exact settings.

Every twin reports `wx=off`. `data/protection_maps/mpu_wxoff_*.txt` shows its live configuration (MPU disabled, PMP or PMS unlocked).

### PSoC 62 memory-topology diagnostic

```bash
west build -p -b $M4 $APP -d build-m4-fetch -- -DEXTRA_CONF_FILE="$LM4" -DCONFIG_PQC_FETCHTEST=y
python3 scripts/console_cmd.py --port <port> --out memtopo.txt --cmd memtopo
python3 scripts/memtopo_check.py memtopo.txt
```

This image is diagnostic only and is never measured.

## Data

- `data/timing/pilot_v7_{pico,m4,lx7}.csv`: one row per image and operation. The main columns are:
  - `time_mean_us`, `time_sd_us` (over all 300 iterations), `time_round_sd_us` (between the 3 round means), median, p95, min and max;
  - `stack_peak_kb`, `heap_peak_kb`;
  - the key, ciphertext and shared-secret sizes.
- `*.raw`: every timed iteration, as streamed by the board.
- `data/summary/layer1_results_v7.xlsx`: every table of the paper. Sheet `README` explains each sheet. Sheets named `Old_*` hold superseded runs and are kept only for the record.
- `data/summary/layer1_results_wxoff_control.xlsx`: protected vs. unprotected execution times.

The file names keep the `pilot_v7` label that the sweep script writes; `v7` is the final dataset.

## Regenerate the figures

```bash
python3 scripts/figures/fig_pq_vs_classical.py
python3 scripts/figures/fig_isa.py
python3 scripts/figures/fig_placement.py
```

The output is written to `figures/`.

## Licence

- Firmware, scripts and figure scripts: Apache License 2.0 (`LICENSE`).
- Measurement data in `data/` and the figures in `figures/`: Creative Commons Attribution 4.0 International (`data/LICENSE`).

## Notes

- Build outputs (ELF files, `build-*` folders) are not included, because they embed host paths. Rebuild them with the commands above.
- wolfSSL is licensed under GPLv3, and Zephyr under Apache-2.0. Neither is included here: `west` fetches Zephyr, and wolfSSL is cloned in Prerequisites, step 4.
- This is research firmware. Its serial console is unauthenticated and includes fault-injection commands (`faultw`, `faultx`, ...) that deliberately crash the board. Do not use it in a deployed device.
- `firmware/prj.conf` and the RP2350 Cortex-M33 and ESP32-S3 board files still hold network and Wi-Fi settings, with placeholder values. Zephyr reads these files in every build, so they were left as measured; the `*_l1only.conf` overlays and `config/overlay-l1only.conf` switch networking off.
