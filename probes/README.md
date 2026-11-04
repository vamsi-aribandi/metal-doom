# Actual M4 runtime probes (2026-11-04)

Machine: Apple M4, arm64 macOS26.5.1 (25F80); Command Line Tools only, Swift6.3.3, runtime Metal compiler present. `xcrun --find metal` fails; Objective-C++ host compilation with `xcrun clang++ -framework Foundation -framework Metal` succeeds.

`probe.mm` compiles MSL from a file, links visible functions, creates a compute pipeline with maxCallStackDepth64 and dispatches one GPU thread. `features.metal` computes an indirect function pointer square(5)=25 and recursive factorial(5)=120. Both MSL3.2 (196610) and MSL4.0 (262144) complete successfully. No CPU emulation.

Reported device limits: unified memory yes, maxBufferLength9534832640 bytes, maxThreadgroupMemoryLength32768 bytes, threadExecutionWidth32, maxTotalThreadsPerThreadgroup1024, supportsDynamicLibraries yes, supportsFunctionPointers yes, Apple GPU family9 yes and10 no.

MSL4.1 (262145) is rejected by the installed runtime compiler. Generic unqualified pointer functions are rejected in MSL4.0 with `pointer type must have explicit address space qualifier`. Explicit `device` pointers inside structs compile and run. Goto/labels rejected with `labeled statements are not supported in Metal` and `'goto' is not supported in Metal`.

Relevant official Apple sources:

- https://developer.apple.com/metal/Metal-Shading-Language-Specification.pdf
- https://developer.apple.com/videos/play/wwdc2020/10013/ (recursion, function pointers, call-stack depth)
- https://developer.apple.com/documentation/metal/writing-reusable-gpu-functions-with-generic-pointers (MSL4.1 feature; unavailable in installed runtime)
- https://developer.apple.com/documentation/metal/mtlcomputepipelinedescriptor/maxcallstackdepth
- https://developer.apple.com/documentation/metal/mtlcomputepipelinedescriptor/linkedfunctions
- https://developer.apple.com/documentation/metal/mtldevice/makelibrary(source:options:)

Target metal-megakernel HEAD74b72a9d9567f4503650cd48f8a0bfff9ba62785 uses one persistent96-thread threadgroup (three independent32-thread SIMD groups), language3.2, runtime source compilation. It uses undocumented coherent(system) host/GPU mailbox synchronization; keep that experimental mechanism isolated from the standalone engine and document that separate integration risk. Doom can occupy one SIMD group, with lane0 performing initially sequential initialization/ticks/render and other lanes inactive, provided there are no full-threadgroup barriers in Doom. Ordinary callable MSL functions with explicit memory/instance pointers are suitable; kernel entry is a thin standalone dispatcher only.
