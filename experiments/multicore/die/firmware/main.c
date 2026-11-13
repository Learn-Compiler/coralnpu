#include "platform.h"
#include "coral_image.h" // Generated from the separately LLVM-compiled Coral kernel.

void control_main(void) {
  const volatile BootConfig *cfg=(const volatile BootConfig *)(uintptr_t)BOOT_CONFIG;
  const unsigned n=cfg->elements,rounds=cfg->rounds,mode=cfg->mode;
  if(cfg->scenario>=5)queued_main(cfg->scenario,n,mode,cfg->wait_cycles);
  if(cfg->scenario) {
    if(read32(SYSTEM_BASE+SYS_ID)!=0x434f5241 || cfg->scenario>4 ||
       cfg->rows<1 || cfg->rows>8 || mode || cfg->wait_cycles==0)die_finish(DIE_BAD_CONFIG);
    complex_main(cfg->scenario,cfg->rows,cfg->wait_cycles);
  }
  if(read32(SYSTEM_BASE+SYS_ID)!=0x434f5241 || n<1 || n>128 ||
     rounds<1 || rounds>2 || mode>3 || cfg->wait_cycles==0)die_finish(DIE_BAD_CONFIG);
  write32(SYSTEM_BASE+SYS_PHASE,1);
  if(mode==1) {
    // Verify that the unmapped-window error path responds instead of hanging.
    if(read32(0x60000000)!=0xdeadbeef || !read32(SYSTEM_BASE+SYS_BUS_ERROR))
      die_finish(DIE_BAD_RESULT);
    write32(SYSTEM_BASE+SYS_BUS_ERROR,1); // W1C.
  }
  coral_load(coral_image,CORAL_IMAGE_WORDS,CORAL_IMAGE_ENTRY);
  if(mode==2)write32(coral_address(0,0),0xffffffff); // Deliberate illegal Coral instruction.
  if(mode==3)write32(coral_address(0,0),0x0000006f); // Deliberate non-completing kernel.
  write32(SYSTEM_BASE+SYS_PHASE,2);
  for(unsigned i=0;i<n;++i) {
    write32(SHARED_BASE+4*i,3*i+1);
    write32(SHARED_BASE+0x400+4*i,7*i+2);
    write32(SHARED_BASE+0x800+4*i,0xdeadbeef);
    write32(SHARED_BASE+0x1000+4*i,0xdeadbeef);
  }
  write32(SHARED_BASE+0x7fc,0xa5a5a5a5);
  write32(SHARED_BASE+0x800+4*n,0x5a5a5a5a);
  write32(SHARED_BASE+0xffc,0xa5a5a5a5);
  write32(SHARED_BASE+0x1000+4*n,0x5a5a5a5a);
  for(unsigned op=0;op<rounds;++op) {
    write32(SYSTEM_BASE+SYS_PHASE,3+op*2);
    const uint32_t a=SHARED_BASE+(op?0x800:0);
    const uint32_t c=SHARED_BASE+(op?0x1000:0x800);
    coral_launch_add(a,SHARED_BASE+0x400,c,n,op);
    const uint32_t error=coral_wait(cfg->wait_cycles);
    if(error)die_finish(error);
    write32(SYSTEM_BASE+SYS_PHASE,4+op*2);
    for(unsigned i=0;i<n;++i)
      if(read32(c+4*i)!=(op?17*i+5:10*i+3))die_finish(DIE_BAD_RESULT);
  }
  uint32_t checksum=0;
  const uint32_t output=SHARED_BASE+(rounds==2?0x1000:0x800);
  for(unsigned i=0;i<n;++i)checksum+=read32(output+4*i);
  if(read32(SHARED_BASE+0x7fc)!=0xa5a5a5a5 ||
     read32(SHARED_BASE+0x800+4*n)!=0x5a5a5a5a ||
     read32(SHARED_BASE+0xffc)!=0xa5a5a5a5 ||
     read32(SHARED_BASE+0x1000+4*n)!=0x5a5a5a5a)die_finish(DIE_BAD_RESULT);
  write32(SYSTEM_BASE+SYS_CHECKSUM,checksum);
  die_finish(DIE_PASS);
}
