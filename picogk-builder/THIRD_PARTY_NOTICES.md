# Third-party notices

This image contains software from the projects below. Each remains under its own licence. Licence texts copied from the pinned sources are in `/licenses` inside the image. Debian package copyright files are in `/usr/share/doc/<package>/copyright`.

| Component | Use | Licence | Where |
|---|---|---|---|
| PicoGK and PicoGK Runtime (LEAP 71) | Geometry kernel | Apache-2.0 | `/licenses/PicoGKRuntime-LICENSE` |
| OpenVDB (Academy Software Foundation) | Voxel library, linked into the runtime | Apache-2.0 | `/licenses/OpenVDB-LICENSE` |
| GLFW | Linked into the runtime, not used headless | zlib | `/licenses/GLFW-LICENSE.md` |
| Dear ImGui | Linked into the runtime, not used headless | MIT | `/licenses/imgui-LICENSE.txt` |
| .NET runtime (Microsoft) | Bundled with the builder | MIT | https://github.com/dotnet/runtime/blob/main/LICENSE.TXT |
| SkiaSharp | PicoGK dependency | MIT | https://github.com/mono/SkiaSharp/blob/main/LICENSE.md |
| libstdc++, libgcc_s (GCC 15, from the `gcc:15` image) | C++ runtime | GPL-3.0 with GCC Runtime Library Exception | https://gcc.gnu.org/onlinedocs/libstdc++/manual/license.html |
| Debian 13 packages, including TBB, Blosc, Boost.Iostreams, Python, NumPy, SciPy, Pydantic | Runtime libraries and mesh checks | See each package | `/usr/share/doc/<package>/copyright` |

Source for the pinned runtime: `https://github.com/leap71/PicoGKRuntime` at commit `0f26321c18ed878a7820ef769c38fd5d49d39242`, with the one-line library-name change in `spike-picogk/Dockerfile.runtime`, which is the file that builds it.

This list was compiled by hand for a small test release. It has not had a legal review.
