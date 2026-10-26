# The SSBU shader translator, ready-made

`totk_xlate` translates Maxwell shader code through SPIR-V and SPIRV-Cross to Metal Shading Language and reflection.
The executable name is retained for the existing host protocol. It is compiled with .NET NativeAOT for arm64 macOS,
so building and running the app needs no .NET runtime.

Sources live in `games/ssbu/13.0.5_online/tools/totk_xlate/`, copied from `games/totk/tools/totk_xlate/` at `1b60feb` (MIT, see LICENSE).
The build copies the repository's `vendor/ryujinx` subset and applies the five patches in SSBU's tool directory.
It does not change another game's source or executable.

SSBU's accessor reports no non-constant texture-offset support. The translator's existing texture pass therefore lowers
dynamic Maxwell gather offsets before emitting SPIR-V. `ConstOffset(s)` operands require constants; reporting support
previously emitted function-local loads inside `OpConstantComposite`, and SPIRV-Cross rejected the shader with `nullptr`.

Rebuild with .NET SDK **10.0.301** (network access may be needed for NuGet / NativeAOT packages):

```sh
games/ssbu/13.0.5_online/tools/totk_xlate/publish_native.sh /path/to/dotnet
```

The script publishes this executable and `totk_xlate.sha256`. SSBU's `Makefile` verifies the hash before installing it
as `SSBU_build/build/shader-service/totk_xlate`. NativeAOT builds can differ byte for byte across source folders.

`tests/test_shader_translation.py` generates a synthetic three-instruction Maxwell shader with uniform-driven gather
offsets. It checks translation through the real service and SPIRV-Cross, four resulting gathers, and SPIR-V validation
when `spirv-val` is installed. The former service fails this fixture with the original `nullptr` exception.

The SSBU cache identity is `ssbu-native-v1-dynamic-offsets` (`third_party/d2r_gfx/gfx/nvn/dmsh_chain.h`), so earlier
translations are kept separately and cannot mask this change.
