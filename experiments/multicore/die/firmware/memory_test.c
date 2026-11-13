#include <stddef.h>
#include <stdint.h>
void *memcpy(void *,const void *,size_t);
void *memset(void *,int,size_t);

/* Runs on the control CPU. Word comparisons independently check that native
 * SB/SH preserve other lanes and that byte libcalls can copy arbitrary offsets. */
unsigned runtime_memory_test(void) {
  volatile uint32_t src[4]={0x04030201,0x08070605,0x0c0b0a09,0x100f0e0d};
  volatile uint32_t dst[4]={0xa5a5a5a5,0xa5a5a5a5,0xa5a5a5a5,0xa5a5a5a5};
  ((volatile uint16_t *)dst)[1]=0x1234;
  if(dst[0]!=0x1234a5a5)return 1;
  ((volatile uint8_t *)dst)[1]=0x5a;
  if(dst[0]!=0x12345aa5)return 2;
  for(unsigned i=0;i<4;++i)dst[i]=0xa5a5a5a5;
  memcpy((unsigned char *)dst+3,(const unsigned char *)src+1,11);
  if(dst[0]!=0x02a5a5a5 || dst[1]!=0x06050403 || dst[2]!=0x0a090807 || dst[3]!=0xa5a50c0b)return 3;
  for(unsigned i=0;i<4;++i)dst[i]=0xa5a5a5a5;
  memset((unsigned char *)dst+1,0x3c,6);
  if(dst[0]!=0x3c3c3ca5 || dst[1]!=0xa53c3c3c || dst[2]!=0xa5a5a5a5)return 4;
  return 0;
}
