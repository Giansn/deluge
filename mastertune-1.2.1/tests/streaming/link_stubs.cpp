// Host test: symbols the container code references for error reporting and culling
#include <cstdio>
#include <cstdlib>
char miscStringBuffer[256];
extern "C" void freezeWithError(char const* e) { printf("freezeWithError %s\n", e); abort(); }
namespace AudioEngine { bool bypassCulling = false; }
