#include <metal_stdlib>
using namespace metal;
[[visible]] uint square(uint x) { return x*x; }
[[visible]] uint triple(uint x) { return x*3; }
[[visible]] uint rec(uint x) { return x<2?1:x*rec(x-1); }
typedef uint (*Fn)(uint);
struct State {Fn fn; device uint *p;};
kernel void probe(device uint*x [[buffer(0)]]) {
 State s; s.fn=x[0]>4?square:triple; s.p=x;
 x[1]=s.fn(x[0]); x[2]=rec(x[0]);
 x[3]=42;
}
