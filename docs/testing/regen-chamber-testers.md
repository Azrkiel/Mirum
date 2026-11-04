# Testing the Mirum regen chamber generator

You give this tool the dimensions of a regeneratively cooled rocket chamber. It builds a 3D mesh, checks the geometry against those dimensions, and writes an STL and a report.

It runs on your own computer in Docker with networking turned off, so your specs and results stay on your machine.

> **Safety: do not manufacture or fire anything based on this output.** The tool checks geometry only. It does no gas dynamics, thermal, structural, or manufacturing analysis, and it can't tell you whether a chamber would survive firing. An "accepted" result only means the mesh matches the spec. The contour is also a simplified curve, not an optimized nozzle.

## What you need

- Docker (Docker Desktop on Windows or Mac, or Docker Engine on Linux). Start it before you run anything.
- An x86-64 computer. Apple Silicon is untested and may give different results or fail.
- About 2 GB of free disk space and 2 GB of memory available to Docker for the examples.

So far this has only been run on one Windows 11 machine. Reports from other setups are especially useful.

## Run it

1. Download the image (824 MB once unpacked):

   ```
   docker pull ghcr.io/azrkiel/mirum-regen:slice-1
   ```

2. Make a new folder and save these example specs in it:
   - [reference.json](https://raw.githubusercontent.com/Azrkiel/Mirum/engineering/regen-chamber/picogk-builder/examples/reference.json): should be accepted
   - [small.json](https://raw.githubusercontent.com/Azrkiel/Mirum/engineering/regen-chamber/picogk-builder/examples/small.json): should be accepted
   - [too_many_channels.json](https://raw.githubusercontent.com/Azrkiel/Mirum/engineering/regen-chamber/picogk-builder/examples/too_many_channels.json): should be rejected

3. Open a terminal in that folder and run this. It works in PowerShell, macOS, and Linux shells:

   ```
   docker run --rm --network none -v "${PWD}:/work" ghcr.io/azrkiel/mirum-regen:slice-1 /work/reference.json /work/output
   ```

   It took about a minute on the test machine. The `NOTICE` line is the safety warning above, printed on every run.

4. Check the result. The printed `stl_sha256` for `reference.json` should be:

   ```
   e1ade11bd1b3bfc23be0ca6ecccbf3cd9412ef6413cb987c191db3d6378e0758
   ```

   If yours differs, please report it. That's a useful finding, not a mistake on your part.

5. Use a new output folder name for each run. Open `output/cleaned.stl` in any STL viewer. The file is about 100 MB, so some viewers are slow with it.

## What comes out

| Exit code | Meaning | Files written |
|---|---|---|
| 0 | Accepted: the mesh passed every check | `cleaned.stl`, `report.json`, `spec.canonical.json` |
| 2 | Rejected: invalid spec, or the mesh failed a check | `rejected_report.json` if a mesh failed; nothing if the spec was invalid |
| 1 | Failed to run: crash, timeout, or bad builder output | Nothing |

To see the exit code, run `echo $LASTEXITCODE` in PowerShell or `echo $?` in macOS and Linux shells, straight after the run.

`report.json` lists each check, the measured walls and ribs, the file hashes, and any failures. `engineering_certified` is always `false`.

## Try your own design

Copy an example and change the numbers. All lengths are in millimetres.

| Field | Meaning |
|---|---|
| `chamber_radius_mm`, `chamber_length_mm` | Inner radius and length of the straight chamber |
| `convergent_length_mm` | Length of the taper down to the throat |
| `throat_radius_mm` | Inner radius at the throat |
| `divergent_length_mm`, `exit_radius_mm` | Length of the nozzle and its inner radius at the exit |
| `wall_thickness_mm` | Total wall thickness; channels run through its middle |
| `channel_count`, `channel_radius_mm` | Number of cooling channels and their radius |
| `channel_twist_rad_per_mm` | How fast the channels spiral |
| `manifold_radius_mm`, `end_margin_mm` | Radius of the manifold rings and their distance from each end |
| `voxel_size_mm` | Mesh resolution, 0.1 to 1.0. Smaller is finer but much slower |

Leave `family`, `units`, and `assumptions` unchanged. Invalid specs are rejected with a message explaining why.

Start at 0.4 mm. A 0.2 mm run took about 2.5 GB of memory and produced an STL of several hundred MB. Below that, the cost grows quickly and is untested. The tool stops a build after 10 minutes.

## What to send back

Open an issue at <https://github.com/Azrkiel/Mirum/issues> with:

1. Your operating system, CPU type, and Docker version.
2. Whether the `reference.json` hash matched.
3. Anything that crashed, hung, or gave an unclear error, with the output and your spec.
4. Any spec that was accepted but looks wrong in a viewer, or rejected when you think it's valid. These are the most valuable reports.
5. Whether the checks and envelope make sense for parts you'd want to design, and what's missing.

GitHub issues are public, so don't include anything confidential.

## Licences

Third-party notices are inside the image at `/licenses`.
