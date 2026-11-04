#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <fstream>
#include <sstream>
int main(int argc,char**argv){@autoreleasepool{
 id<MTLDevice>d=MTLCreateSystemDefaultDevice(); if(!d){puts("NO DEVICE");return 1;}
 printf("name=%s unified=%d maxBuffer=%lu maxThreadgroupMemory=%lu threads=(%lu,%lu,%lu) dynamicLibraries=%d functionPointers=%d\n",d.name.UTF8String,d.hasUnifiedMemory,(unsigned long)d.maxBufferLength,(unsigned long)d.maxThreadgroupMemoryLength,(unsigned long)d.maxThreadsPerThreadgroup.width,(unsigned long)d.maxThreadsPerThreadgroup.height,(unsigned long)d.maxThreadsPerThreadgroup.depth,d.supportsDynamicLibraries,d.supportsFunctionPointers);
 for(int i=1001;i<=1010;i++)printf("family%d=%d ",i,[d supportsFamily:(MTLGPUFamily)i]);puts("");
 std::ifstream f(argv[1]);std::stringstream ss;ss<<f.rdbuf(); NSString* src=[NSString stringWithUTF8String:ss.str().c_str()];
 MTLCompileOptions*o=[MTLCompileOptions new];o.languageVersion=(MTLLanguageVersion)strtol(argc>2?argv[2]:"262144",0,0);o.fastMathEnabled=NO;
 NSError*e=nil;id<MTLLibrary>l=[d newLibraryWithSource:src options:o error:&e]; if(!l){printf("COMPILE FAIL: %s\n",e.description.UTF8String);return 2;}puts("COMPILE OK");
 MTLComputePipelineDescriptor*p=[MTLComputePipelineDescriptor new];p.computeFunction=[l newFunctionWithName:@"probe"];p.maxCallStackDepth=64; NSMutableArray *linked=[NSMutableArray array]; for(NSString *name in l.functionNames) if(![name isEqualToString:@"probe"]) { id<MTLFunction> fn=[l newFunctionWithName:name]; if(fn.functionType==MTLFunctionTypeVisible) [linked addObject:fn]; } MTLLinkedFunctions *lf=[MTLLinkedFunctions new]; lf.functions=linked; p.linkedFunctions=lf;
 id<MTLComputePipelineState>ps=[d newComputePipelineStateWithDescriptor:p options:MTLPipelineOptionNone reflection:nil error:&e];if(!ps){printf("PIPELINE FAIL: %s\n",e.description.UTF8String);return 3;}
 printf("PIPELINE OK width=%lu maxThreads=%lu staticTG=%lu\n",(unsigned long)ps.threadExecutionWidth,(unsigned long)ps.maxTotalThreadsPerThreadgroup,(unsigned long)ps.staticThreadgroupMemoryLength);
 id<MTLBuffer>b=[d newBufferWithLength:4096 options:MTLResourceStorageModeShared]; ((uint32_t*)b.contents)[0]=5;
 id<MTLCommandBuffer>c=[[d newCommandQueue] commandBuffer];id<MTLComputeCommandEncoder>enc=[c computeCommandEncoder];[enc setComputePipelineState:ps];[enc setBuffer:b offset:0 atIndex:0];[enc dispatchThreads:MTLSizeMake(1,1,1) threadsPerThreadgroup:MTLSizeMake(1,1,1)];[enc endEncoding];[c commit];[c waitUntilCompleted];
 printf("RUN status=%lu error=%s output=%u,%u,%u,%u gpuMS=%g\n",(unsigned long)c.status,c.error.description.UTF8String,((uint32_t*)b.contents)[0],((uint32_t*)b.contents)[1],((uint32_t*)b.contents)[2],((uint32_t*)b.contents)[3],(c.GPUEndTime-c.GPUStartTime)*1000);
 return c.error?4:0;
}}
