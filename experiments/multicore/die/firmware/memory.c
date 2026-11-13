#include <stddef.h>
/* LLVM may emit these helpers for aggregate initialization/copy even in a
 * freestanding program. Volatile accesses prevent recursive libcall folding. */
void *memcpy(void *dst,const void *src,size_t bytes) {
  if(!(((size_t)dst|(size_t)src|bytes)&3u)) {
    volatile unsigned *d=(volatile unsigned *)dst;
    const volatile unsigned *s=(const volatile unsigned *)src;
    for(size_t i=0;i<bytes/4;++i)d[i]=s[i];
    return dst;
  }
  volatile unsigned char *d=(volatile unsigned char *)dst;
  const volatile unsigned char *s=(const volatile unsigned char *)src;
  for(size_t i=0;i<bytes;++i)d[i]=s[i];
  return dst;
}
void *memset(void *dst,int value,size_t bytes) {
  volatile unsigned char *d=(volatile unsigned char *)dst;
  for(size_t i=0;i<bytes;++i)d[i]=(unsigned char)value;
  return dst;
}
