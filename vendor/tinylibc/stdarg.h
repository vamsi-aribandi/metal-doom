#ifndef TL_STDARG_H
#define TL_STDARG_H
/* Use compiler ABI for wasm32. Adapted from Bobliuuu's MIT tinylibc. */
typedef __builtin_va_list va_list;
#define va_start(ap, last) __builtin_va_start(ap, last)
#define va_end(ap) __builtin_va_end(ap)
#define va_copy(d, s) __builtin_va_copy(d, s)
#define va_arg(ap, T) __builtin_va_arg(ap, T)
#endif
