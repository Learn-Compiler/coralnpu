#include "platform.h"
#include "complex_image.h"

void complex_main(unsigned parallel,unsigned rows,uint32_t timeout_cycles) {
  write32(SYSTEM_BASE+SYS_PHASE,10);
  coral_load(complex_image,COMPLEX_IMAGE_WORDS,COMPLEX_IMAGE_ENTRY);
  for(unsigned i=0;i<rows*GEMM_K;++i)
    write32(SHARED_BASE+GEMM_A+4*i,(int32_t)((i*3+1)%13)-6);
  for(unsigned i=0;i<GEMM_K*GEMM_N;++i)
    write32(SHARED_BASE+GEMM_B+4*i,(int32_t)((i*5+2)%11)-5);
  for(unsigned i=0;i<GEMM_N;++i)write32(SHARED_BASE+GEMM_BIAS+4*i,(int32_t)i-6);
  for(unsigned i=0;i<CONV_IH*CONV_IW*CONV_CI;++i)
    write32(SHARED_BASE+CONV_X+4*i,(int32_t)((i*7+3)%17)-8);
  for(unsigned i=0;i<CONV_CO*3*3*CONV_CI;++i)
    write32(SHARED_BASE+CONV_W+4*i,(int32_t)((i*3+2)%7)-3);
  for(unsigned i=0;i<CONV_CO;++i)write32(SHARED_BASE+CONV_BIAS+4*i,(int32_t)i*5-8);
  for(unsigned i=0;i<rows*GEMM_N;++i)write32(SHARED_BASE+GEMM_Y+4*i,0xdeadbeef);
  for(unsigned i=0;i<rows*CONV_OW*CONV_CO;++i)write32(SHARED_BASE+CONV_Y+4*i,0xdeadbeef);
  write32(SHARED_BASE+GEMM_Y-4,0xa5a5a5a5);
  write32(SHARED_BASE+GEMM_Y+4*rows*GEMM_N,0x5a5a5a5a);
  write32(SHARED_BASE+CONV_Y-4,0xa5a5a5a5);
  write32(SHARED_BASE+CONV_Y+4*rows*CONV_OW*CONV_CO,0x5a5a5a5a);
  coral_prepare_complex(rows);
  write32(SYSTEM_BASE+SYS_PHASE,11);
  coral_start_mask(0x3);
  uint32_t error;
  if(!parallel && (error=coral_wait_mask(0x3,timeout_cycles)))die_finish(error);
  write32(SYSTEM_BASE+SYS_PHASE,12);
  coral_start_mask(0xc);
  if((error=coral_wait_mask(0xf,timeout_cycles)))die_finish(error);
  write32(SYSTEM_BASE+SYS_PHASE,13);
  uint32_t checksum=0;
  for(unsigned i=0;i<rows*GEMM_N;++i)checksum+=read32(SHARED_BASE+GEMM_Y+4*i);
  for(unsigned i=0;i<rows*CONV_OW*CONV_CO;++i)checksum+=read32(SHARED_BASE+CONV_Y+4*i);
  write32(SYSTEM_BASE+SYS_CHECKSUM,checksum);
  die_finish(DIE_PASS);
}
