// Native Metal host. Doom initialization, simulation and drawing execute on GPU.
#import <Cocoa/Cocoa.h>
#import <Metal/Metal.h>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <sstream>
#include <vector>
#include <string>
#include <stdexcept>

static constexpr uint32_t MemoryBytes=64u*1024u*1024u;
static constexpr uint32_t Width=320, Height=200;
static std::string boundedError(NSError *error);
static std::string readText(NSString *path) {
 NSError *error=nil;NSString *s=[NSString stringWithContentsOfFile:path encoding:NSUTF8StringEncoding error:&error];
 if(!s)throw std::runtime_error(boundedError(error));return s.UTF8String;
}
static NSData *readData(NSString *path) {
 NSData *d=[NSData dataWithContentsOfFile:path];if(!d)throw std::runtime_error("Cannot read "+std::string(path.UTF8String));return d;
}
static std::string boundedError(NSError *error) {
 std::string text=error ? error.description.UTF8String : "Unknown Metal error";
 constexpr size_t limit=40u*1024u;
 if(text.size()>limit){size_t total=text.size();text.resize(limit);text+="\n[Diagnostic truncated; total "+std::to_string(total)+" bytes]";}
 return text;
}
static uint32_t little32(const uint8_t *p) {
 return uint32_t(p[0])|(uint32_t(p[1])<<8)|(uint32_t(p[2])<<16)|(uint32_t(p[3])<<24);
}
static NSData *readWad(NSString *path) {
 // Validate only the asset container. The original Doom engine interprets lumps.
 constexpr uint64_t limit=64u*1024u*1024u;
 NSError *error=nil;NSDictionary *attributes=[[NSFileManager defaultManager]attributesOfItemAtPath:path error:&error];
 if(!attributes)throw std::runtime_error("Cannot inspect IWAD: "+boundedError(error));
 uint64_t bytes=[attributes fileSize];if(bytes<12||bytes>limit)throw std::runtime_error("IWAD must be between 12 bytes and 64 MiB");
 NSData *data=readData(path);const uint8_t *p=(const uint8_t*)data.bytes;
 if(data.length<12||data.length>limit)throw std::runtime_error("IWAD size changed while reading");
 if(memcmp(p,"IWAD",4))throw std::runtime_error("Expected an IWAD base game file (header IWAD)");
 uint32_t count=little32(p+4),directory=little32(p+8);
 if(!count||directory<12||uint64_t(directory)+uint64_t(count)*16>data.length)
  throw std::runtime_error("Invalid IWAD directory bounds");
 for(uint32_t i=0;i<count;++i){const uint8_t *entry=p+directory+uint64_t(i)*16;uint32_t offset=little32(entry),size=little32(entry+4);
  if(uint64_t(offset)+size>data.length)throw std::runtime_error("IWAD lump "+std::to_string(i)+" exceeds file bounds");
 }
 return data;
}
static void writeBytes(NSString *path,const void*p,size_t n) {
 NSData*d=[NSData dataWithBytes:p length:n];if(![d writeToFile:path atomically:YES])throw std::runtime_error("Cannot write output");
}
class Engine {
public:
 id<MTLDevice> device;id<MTLCommandQueue> queue;id<MTLLibrary> library;
 id<MTLComputePipelineState> pipeline;
 id<MTLBuffer> memory,mailbox,instance;
 uint32_t *control; uint32_t previousKeys=0; double gpuMS=0;unsigned dispatches=0;
 Engine(NSString *shader) {
 device=MTLCreateSystemDefaultDevice();if(!device)throw std::runtime_error("A Metal device is required");
 queue=[device newCommandQueue];MTLCompileOptions *options=[MTLCompileOptions new];options.languageVersion=MTLLanguageVersion3_2;options.mathMode=MTLMathModeSafe;
 NSError *error=nil;library=[device newLibraryWithSource:[NSString stringWithUTF8String:readText(shader).c_str()] options:options error:&error];
 if(!library)throw std::runtime_error(boundedError(error));
 NSMutableArray<id<MTLFunction>> *visible=[NSMutableArray array];for(NSString*name in library.functionNames){id<MTLFunction>f=[library newFunctionWithName:name];if(f.functionType==MTLFunctionTypeVisible)[visible addObject:f];}
 MTLLinkedFunctions *links=[MTLLinkedFunctions new];links.functions=visible;
 MTLComputePipelineDescriptor *desc=[MTLComputePipelineDescriptor new];desc.computeFunction=[library newFunctionWithName:@"doom_dispatch"];if(!desc.computeFunction)throw std::runtime_error("Missing Metal entry doom_dispatch");desc.linkedFunctions=links;desc.maxCallStackDepth=32;
 pipeline=[device newComputePipelineStateWithDescriptor:desc options:MTLPipelineOptionNone reflection:nil error:&error];if(!pipeline)throw std::runtime_error(boundedError(error));
 mailbox=[device newBufferWithLength:256 options:MTLResourceStorageModeShared];instance=[device newBufferWithLength:1024*1024 options:MTLResourceStorageModeShared];
 memset(mailbox.contents,0,256);memset(instance.contents,0,instance.length);control=(uint32_t*)mailbox.contents;
 fprintf(stderr,"Metal device: %s; SIMD width %lu\n",device.name.UTF8String,(unsigned long)pipeline.threadExecutionWidth);
 }
 uint32_t invoke(uint32_t operation,uint32_t a=0,uint32_t b=0){control[0]=operation;control[1]=a;control[2]=b;control[13]=0;
 id<MTLCommandBuffer>cb=[queue commandBuffer];id<MTLComputeCommandEncoder>enc=[cb computeCommandEncoder];[enc setComputePipelineState:pipeline];[enc setBuffer:memory offset:0 atIndex:0];[enc setBuffer:mailbox offset:0 atIndex:1];[enc setBuffer:instance offset:0 atIndex:2];[enc dispatchThreads:MTLSizeMake(1,1,1) threadsPerThreadgroup:MTLSizeMake(1,1,1)];[enc endEncoding];[cb commit];[cb waitUntilCompleted];if(cb.status!=MTLCommandBufferStatusCompleted)throw std::runtime_error(boundedError(cb.error));gpuMS+=(cb.GPUEndTime-cb.GPUStartTime)*1000;++dispatches;if(control[13])throw std::runtime_error("GPU engine trap "+std::to_string(control[13]));return control[8];}
 void load(NSData *d,NSString *initial){NSData*image=readData(initial);memory=[device newBufferWithLength:MAX(NSUInteger(MemoryBytes),image.length) options:MTLResourceStorageModeShared];memset(memory.contents,0,memory.length);memcpy(memory.contents,image.bytes,image.length);invoke(5);uint32_t offset=invoke(3,(uint32_t)d.length);if(!offset||uint64_t(offset)+d.length>memory.length)throw std::runtime_error("WAD allocation failed");memcpy((uint8_t*)memory.contents+offset,d.bytes,d.length);uint32_t result=invoke(0,offset,(uint32_t)d.length);if(control[12]&&uint64_t(control[11])+control[12]<=memory.length)fwrite((uint8_t*)memory.contents+control[11],1,MIN(control[12],16384u),stderr);if(result)throw std::runtime_error("Doom initialization failed: "+std::to_string(result));}
 void step(uint32_t keys){static const uint32_t keyCodes[]={0xad,0xaf,0xac,0xae,0xa0,0xa1,0xa3,0xa2,13,0xb6,49,50,51,52,53,54,55,9};uint32_t changed=keys^previousKeys;for(unsigned bit=0;bit<18;++bit)if(changed&(1u<<bit))invoke(4,(keys>>bit)&1,keyCodes[bit]);previousKeys=keys;invoke(1);}
 const int32_t *diagnostics(){if(uint64_t(control[10])+64>memory.length)throw std::runtime_error("Invalid diagnostics offset");return (const int32_t*)((uint8_t*)memory.contents+control[10]);}
 uint32_t stateHash(){uint32_t hash=2166136261;const int32_t*d=diagnostics();for(unsigned i=0;i<16;++i){hash^=(uint32_t)d[i];hash*=16777619;}return hash;}
 const uint8_t *pixels(){invoke(2);if(uint64_t(control[9])+Width*Height*4>memory.length)throw std::runtime_error("Invalid GPU framebuffer");return (uint8_t*)memory.contents+control[9];}
};

@interface DoomView:NSView
@property(nonatomic) uint32_t keys;
@property(nonatomic,strong) NSBitmapImageRep *bitmap;
@end
@implementation DoomView
-(BOOL)acceptsFirstResponder{return YES;}
-(void)drawRect:(NSRect)dirty{[[NSColor blackColor]setFill];NSRectFill(self.bounds);if(self.bitmap){[NSGraphicsContext currentContext].imageInterpolation=NSImageInterpolationNone;NSSize size=self.bounds.size;CGFloat scale=MIN(size.width/320.0,size.height/240.0);NSRect rect=NSMakeRect((size.width-320*scale)/2,(size.height-240*scale)/2,320*scale,240*scale);[self.bitmap drawInRect:rect];}}
-(uint32_t)mask:(unsigned short)c{switch(c){case 13:case 126:return 1;case 1:case 125:return 2;case 0:case 123:return 4;case 2:case 124:return 8;case 12:return 16;case 14:return 32;case 49:return 64;case 36:return 128;case 6:return 256;case 56:case 60:return 512;case 18:return 1u<<10;case 19:return 1u<<11;case 20:return 1u<<12;case 21:return 1u<<13;case 23:return 1u<<14;case 22:return 1u<<15;case 26:return 1u<<16;case 48:return 1u<<17;default:return 0;}}
-(void)keyDown:(NSEvent*)e{if(e.keyCode==53){[NSApp terminate:nil];return;}self.keys|=[self mask:e.keyCode];}
-(void)keyUp:(NSEvent*)e{self.keys&=~[self mask:e.keyCode];}
-(void)flagsChanged:(NSEvent*)e{if(e.modifierFlags&NSEventModifierFlagShift)self.keys|=512;else self.keys&=~512;}
@end
@interface AppDelegate:NSObject<NSApplicationDelegate,NSWindowDelegate>
@property(nonatomic,strong) NSWindow *window;
@property(nonatomic,strong) DoomView *view;
@property(nonatomic,strong) NSTimer *timer;
@property(nonatomic) Engine *engine;
@property(nonatomic) std::chrono::steady_clock::time_point last;
@property(nonatomic) double accumulator;
@end
@implementation AppDelegate
-(void)applicationDidFinishLaunching:(NSNotification*)n{self.window=[[NSWindow alloc]initWithContentRect:NSMakeRect(0,0,960,720) styleMask:NSWindowStyleMaskTitled|NSWindowStyleMaskClosable|NSWindowStyleMaskResizable backing:NSBackingStoreBuffered defer:NO];self.window.title=@"Metal Doom — W/S move · A/D turn · Q/E strafe · Space fire · Enter use · Esc quit";self.window.delegate=self;self.view=[[DoomView alloc]initWithFrame:self.window.contentView.bounds];self.view.autoresizingMask=NSViewWidthSizable|NSViewHeightSizable;self.window.contentView=self.view;[self.window makeFirstResponder:self.view];[self.window center];[self.window makeKeyAndOrderFront:nil];[NSApp activateIgnoringOtherApps:YES];self.last=std::chrono::steady_clock::now();self.timer=[NSTimer scheduledTimerWithTimeInterval:1.0/70.0 target:self selector:@selector(update:) userInfo:nil repeats:YES];}
-(BOOL)applicationShouldTerminateAfterLastWindowClosed:(NSApplication*)s{return YES;}
-(void)windowDidResignKey:(NSNotification*)n{self.view.keys=0;}
-(void)update:(NSTimer*)timer{try{auto now=std::chrono::steady_clock::now();self.accumulator+=std::chrono::duration<double>(now-self.last).count();self.last=now;int count=0;while(self.accumulator>=1.0/35.0&&count<4){self.engine->step(self.view.keys);self.accumulator-=1.0/35.0;++count;}if(count){const uint8_t*p=self.engine->pixels();NSBitmapImageRep*b=[[NSBitmapImageRep alloc]initWithBitmapDataPlanes:nullptr pixelsWide:Width pixelsHigh:Height bitsPerSample:8 samplesPerPixel:4 hasAlpha:YES isPlanar:NO colorSpaceName:NSDeviceRGBColorSpace bytesPerRow:Width*4 bitsPerPixel:32];for(unsigned i=0;i<Width*Height;++i){b.bitmapData[i*4]=p[i*4+2];b.bitmapData[i*4+1]=p[i*4+1];b.bitmapData[i*4+2]=p[i*4];b.bitmapData[i*4+3]=255;}self.view.bitmap=b;[self.view setNeedsDisplay:YES];}}catch(const std::exception&e){fprintf(stderr,"%s\n",e.what());[self.timer invalidate];[NSApp terminate:nil];}}
@end
int main(int argc,char **argv){@autoreleasepool{try{
 NSString*shader=@"build/doom.metal",*wad=nil,*initial=@"build/initial-memory.bin",*output=@"build/results";bool headless=false,scripted=false;unsigned ticks=350;
 for(int i=1;i<argc;++i){std::string a=argv[i];auto value=[&](){if(++i>=argc)throw std::runtime_error("Missing value after "+a);return [NSString stringWithUTF8String:argv[i]];};if(a=="--wad")wad=value();else if(a=="--shader")shader=value();else if(a=="--initial-memory")initial=value();else if(a=="--output")output=value();else if(a=="--ticks")ticks=std::stoul(value().UTF8String);else if(a=="--headless")headless=true;else if(a=="--scripted")scripted=true;else if(a=="--help"){puts("metal-doom --wad FILE [--headless --ticks N --scripted --output DIR] [--shader FILE --initial-memory FILE]");return 0;}else throw std::runtime_error("Unknown option "+a);}
 if(![[NSFileManager defaultManager]fileExistsAtPath:shader]){NSString*candidate=[[NSBundle mainBundle].resourcePath stringByAppendingPathComponent:@"doom.metal"];if([[NSFileManager defaultManager]fileExistsAtPath:candidate])shader=candidate;}
 if(![[NSFileManager defaultManager]fileExistsAtPath:initial]){NSString*candidate=[[NSBundle mainBundle].resourcePath stringByAppendingPathComponent:@"initial-memory.bin"];if([[NSFileManager defaultManager]fileExistsAtPath:candidate])initial=candidate;}
 if(!wad&&getenv("DOOM_WAD"))wad=[NSString stringWithUTF8String:getenv("DOOM_WAD")];
 if(!wad&&!headless){[NSApplication sharedApplication];[NSApp setActivationPolicy:NSApplicationActivationPolicyRegular];[NSApp activateIgnoringOtherApps:YES];NSOpenPanel*panel=[NSOpenPanel openPanel];panel.title=@"Select your Doom IWAD";panel.message=@"Choose doom1.wad, doom.wad, or a compatible free IWAD. Game assets are supplied separately.";panel.canChooseFiles=YES;panel.canChooseDirectories=NO;panel.allowsMultipleSelection=NO;if([panel runModal]!=NSModalResponseOK)return 0;wad=panel.URL.path;}
 if(!wad)throw std::runtime_error("Supply --wad /path/to/doom1.wad (game assets are not bundled)");NSData *wadData=readWad(wad);Engine engine(shader);engine.load(wadData,initial);
 if(headless){[[NSFileManager defaultManager]createDirectoryAtPath:output withIntermediateDirectories:YES attributes:nil error:nil];std::ostringstream trace;trace<<"tick,hash,gametic,gamestate,health,armor,x,y,z,angle,kills,items,secrets,rndindex,prndindex,leveltime,weapon,exited\n";auto start=std::chrono::steady_clock::now();for(unsigned i=0;i<ticks;++i){uint32_t keys=scripted?((i<140?1u:0u)|(i>=140&&i<210?8u:0u)|(i>=210&&i<280?64u:0u)|(i==280?128u:0u)):0;engine.step(keys);trace<<i+1<<","<<engine.stateHash();for(unsigned j=0;j<16;++j)trace<<","<<engine.diagnostics()[j];trace<<"\n";}const uint8_t*p=engine.pixels();double seconds=std::chrono::duration<double>(std::chrono::steady_clock::now()-start).count();writeBytes([output stringByAppendingPathComponent:@"frame.rgba"],p,Width*Height*4);std::string csv=trace.str();writeBytes([output stringByAppendingPathComponent:@"ticks.csv"],csv.data(),csv.size());NSString*summary=[NSString stringWithFormat:@"{\"device\":\"%@\",\"ticks\":%u,\"seconds\":%.9f,\"gpu_ms\":%.6f,\"dispatches\":%u,\"state_hash\":%u,\"width\":%u,\"height\":%u}\n",engine.device.name,ticks,seconds,engine.gpuMS,engine.dispatches,engine.stateHash(),Width,Height];writeBytes([output stringByAppendingPathComponent:@"summary.json"],summary.UTF8String,strlen(summary.UTF8String));printf("%s",summary.UTF8String);return 0;}
 [NSApplication sharedApplication];[NSApp setActivationPolicy:NSApplicationActivationPolicyRegular];AppDelegate*delegate=[AppDelegate new];delegate.engine=&engine;[NSApp setDelegate:delegate];[NSApp run];return 0;
 }catch(const std::exception&e){fprintf(stderr,"metal-doom: %s\n",e.what());return 1;}}}
