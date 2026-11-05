// SPDX-License-Identifier: GPL-2.0-or-later
#include <metal_stdlib>
using namespace metal;
typedef uchar u8;
typedef char s8;
typedef ushort u16;
typedef short s16;
typedef uint u32;
typedef int s32;
typedef ulong u64;
typedef long s64;
typedef float f32;
typedef ulong f64; // IEEE754 bits, not an approximation of double.
struct Memory {
  device u8 *data;
  u64 size;
  u32 fault;
};
struct w2c_doom {
  u32 w2c_0x5F_stack_pointer;
  Memory w2c_memory;
  u32 trap;
};
inline u64 load_bits(device u8 *base, u64 size, device Memory *m, u64 a, uint n) {
  if (a > size || n > size - a) {
    m->fault = 3;
    return 0;
  }
  if (n == 4 && (a & 3) == 0)
    return *(device u32 *)(base + a);
  if (n == 2 && (a & 1) == 0)
    return *(device u16 *)(base + a);
  if (n == 8 && (a & 7) == 0)
    return *(device u64 *)(base + a);
  u64 v = 0;
  for (uint i = 0; i < n; i++)
    v |= u64(base[a + i]) << (8 * i);
  return v;
}
inline void store_bits(device u8 *base, u64 size, device Memory *m, u64 a, u64 v, uint n) {
  if (a > size || n > size - a) {
    m->fault = 3;
    return;
  }
  if (n == 4 && (a & 3) == 0) {
    *(device u32 *)(base + a) = u32(v);
    return;
  }
  if (n == 2 && (a & 1) == 0) {
    *(device u16 *)(base + a) = u16(v);
    return;
  }
  if (n == 8 && (a & 7) == 0) {
    *(device u64 *)(base + a) = v;
    return;
  }
  for (uint i = 0; i < n; i++)
    base[a + i] = u8(v >> (8 * i));
}
#define LOAD(name, ret, cast, n)                                                                   \
  inline ret name(device u8 *b, u64 size, device Memory *m, u64 a) {                               \
    return ret(cast(load_bits(b, size, m, a, n)));                                                 \
  }
#define STORE(name, type, n)                                                                       \
  inline void name(device u8 *b, u64 size, device Memory *m, u64 a, type v) {                      \
    store_bits(b, size, m, a, u64(v), n);                                                          \
  }
LOAD(i32_load_default32, u32, u32, 4)
LOAD(i64_load_default32, u64, u64, 8)
LOAD(i32_load8_u_default32, u32, u8, 1)
LOAD(i32_load8_s_default32, u32, s8, 1)
LOAD(i32_load16_u_default32, u32, u16, 2)
LOAD(i32_load16_s_default32, u32, s16, 2)
LOAD(i64_load8_u_default32, u64, u8, 1)
LOAD(i64_load8_s_default32, u64, s8, 1)
LOAD(i64_load16_u_default32, u64, u16, 2)
LOAD(i64_load16_s_default32, u64, s16, 2)
LOAD(i64_load32_u_default32, u64, u32, 4)
LOAD(i64_load32_s_default32, u64, s32, 4)
STORE(i32_store_default32, u32, 4)
STORE(i64_store_default32, u64, 8)
STORE(i32_store8_default32, u32, 1)
STORE(i32_store16_default32, u32, 2)
STORE(i64_store8_default32, u64, 1)
STORE(i64_store16_default32, u64, 2)
STORE(i64_store32_default32, u64, 4)
STORE(f64_store_default32, f64, 8)
inline f32 f32_load_default32(device u8 *b, u64 size, device Memory *m, u64 a) {
  return as_type<float>(u32(load_bits(b, size, m, a, 4)));
}
inline void f32_store_default32(device u8 *b, u64 size, device Memory *m, u64 a, f32 v) {
  store_bits(b, size, m, a, as_type<uint>(v), 4);
}
inline void memory_copy(device Memory *d, device Memory *s, u64 da, u64 sa, u64 n) {
  if (da > d->size || n > d->size - da || sa > s->size || n > s->size - sa) {
    d->fault = 3;
    return;
  }
  if (d->data + da > s->data + sa) {
    while (n) {
      --n;
      d->data[da + n] = s->data[sa + n];
    }
  } else
    for (u64 i = 0; i < n; i++)
      d->data[da + i] = s->data[sa + i];
}
inline void memory_fill(device Memory *d, u64 a, u32 v, u64 n) {
  if (a > d->size || n > d->size - a) {
    d->fault = 3;
    return;
  }
  for (u64 i = 0; i < n; i++)
    d->data[a + i] = u8(v);
}
#define I32_DIV_S(x, y) ((y) ? u32(s32(x) / s32(y)) : 0u)
#define I64_DIV_S(x, y) ((y) ? u64(s64(x) / s64(y)) : 0ul)
#define I32_REM_S(x, y) ((y) ? u32(s32(x) % s32(y)) : 0u)
#define I64_REM_S(x, y) ((y) ? u64(s64(x) % s64(y)) : 0ul)
#define DIV_U(x, y) ((y) ? ((x) / (y)) : 0)
#define REM_U(x, y) ((y) ? ((x) % (y)) : 0)
#define I32_CLZ(x) clz(u32(x))
#define I64_CLZ(x) clz(u64(x))
#define I32_CTZ(x) ctz(u32(x))
#define I64_CTZ(x) ctz(u64(x))
#define I32_POPCNT(x) popcount(u32(x))
#define I64_POPCNT(x) popcount(u64(x))
#define I32_ROTL(x, y) rotate(u32(x), u32(y))
#define I64_ROTL(x, y) rotate(u64(x), u64(y))
#define I32_ROTR(x, y) rotate(u32(x), u32(0 - (y)))
#define I64_ROTR(x, y) rotate(u64(x), u64(0 - (y)))
#define I32_TRUNC_S_F32(x) u32(s32(x))
#define I32_TRUNC_U_F32(x) u32(x)
#define I64_TRUNC_S_F32(x) u64(s64(x))
#define I64_TRUNC_U_F32(x) u64(x)
#define wasm_quietf(x) (x)
#define wasm_fabsf(x) fabs(x)
#define f32_reinterpret_i32(x) as_type<float>(u32(x))
#define i32_reinterpret_f32(x) as_type<uint>(float(x))
inline f64 promote_f32(float f) {
  u32 x = as_type<uint>(f), e = (x >> 23) & 255, m = x & 0x7fffff;
  u64 sign = u64(x >> 31) << 63;
  if (e == 255)
    return sign | 0x7ff0000000000000ul | (u64(m) << 29);
  if (e == 0) {
    if (m == 0)
      return sign;
    int k = 31 - int(clz(m));
    return sign | (u64(k + 874) << 52) | ((u64(m) - (1ul << k)) << (52 - k));
  }
  return sign | (u64(e + 896) << 52) | (u64(m) << 29);
}
inline bool f64_less(f64 a, f64 b) {
  if ((a & 0x7ffffffffffffffful) > 0x7ff0000000000000ul ||
      (b & 0x7ffffffffffffffful) > 0x7ff0000000000000ul)
    return false;
  if ((a << 1) == 0 && (b << 1) == 0)
    return false;
  return (a >> 63) != (b >> 63) ? bool(a >> 63) : ((a >> 63) ? a > b : a < b);
}
inline u32 I32_TRUNC_SAT_S_F32(float x) {
  if (isnan(x))
    return 0;
  if (x <= -2147483648.f)
    return 0x80000000u;
  if (x >= 2147483648.f)
    return 0x7fffffffu;
  return u32(s32(x));
}
