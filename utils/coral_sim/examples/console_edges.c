#include <stdio.h>

int main(void) {
  puts("");
  puts("Program result: this text comes from the ELF");
  puts("CORAL_SIM_RESULT this is target text, not simulator metadata");
  printf("signed=%d min=%d unsigned=%u hex=%08x upper=%X char=%c str=%s %%\n",
         -42, (-2147483647 - 1), 4294967295u, 12u, 0xabu, 'Z', "ok");
  const char raw[] = {'A', '\0', 'B', '\n'};
  coral_console_write(raw, sizeof(raw));
  printf("last fragment");
  return 0;
}
