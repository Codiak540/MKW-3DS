race-profile.tar.gz: GCC profile (.gcda branch and call counts) for
profile-guided optimization, recorded from one scripted Old 3DS race in Azahar
(VS, Mario / Standard Kart M, Luigi Circuit, 11 CPU racers, 1500 steps; race
GPU commands and jobs pinned to the main core).

tools/build.py uses it automatically. It only changes how GCC lays out and
inlines code; simulation and rendering results are identical (checked with the
physics replay test, the CPU racer log and pixel-identical race captures).
Functions changed after the profile was recorded build without it.

  MKW_PGO=off      build without the profile
  tools/o3ds_pgo_profile.py   record a new profile (needs the worktree's
                              Azahar copy in build/azahar-o3ds)
