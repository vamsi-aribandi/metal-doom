// Native Metal host. Doom initialization, simulation and drawing execute on GPU.
#import <Cocoa/Cocoa.h>
#import <Metal/Metal.h>
#import <QuartzCore/QuartzCore.h>
#include <CommonCrypto/CommonDigest.h>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <sstream>
#include <vector>
#include <string>
#include <stdexcept>
#include <thread>
#include "persistent_protocol.h"

static constexpr uint32_t MemoryBytes = 96u * 1024u * 1024u;
static constexpr uint32_t Width = 320, Height = 200;
static std::string sha256(const uint8_t *bytes, size_t length) {
  unsigned char digest[CC_SHA256_DIGEST_LENGTH];
  CC_SHA256(bytes, (CC_LONG)length, digest);
  static constexpr char hex[] = "0123456789abcdef";
  std::string result;
  result.reserve(CC_SHA256_DIGEST_LENGTH * 2);
  for (unsigned char byte : digest) {
    result += hex[byte >> 4];
    result += hex[byte & 15];
  }
  return result;
}
static std::string boundedError(NSError *error);
static std::string readText(NSString *path) {
  NSError *error = nil;
  NSString *s = [NSString stringWithContentsOfFile:path encoding:NSUTF8StringEncoding error:&error];
  if (!s)
    throw std::runtime_error(boundedError(error));
  return s.UTF8String;
}
static NSData *readData(NSString *path) {
  NSData *d = [NSData dataWithContentsOfFile:path];
  if (!d)
    throw std::runtime_error("Cannot read " + std::string(path.UTF8String));
  return d;
}
static std::string boundedError(NSError *error) {
  std::string text = error ? error.description.UTF8String : "Unknown Metal error";
  constexpr size_t limit = 40u * 1024u;
  if (text.size() > limit) {
    size_t total = text.size();
    text.resize(limit);
    text += "\n[Diagnostic truncated; total " + std::to_string(total) + " bytes]";
  }
  return text;
}
static unsigned parseCount(NSString *value, unsigned minimum, unsigned maximum, const char *flag) {
  std::string text = value.UTF8String;
  unsigned count = 0;
  bool valid = !text.empty();
  for (char c : text) {
    if (c < '0' || c > '9' || count > (maximum - unsigned(c - '0')) / 10) {
      valid = false; break;
    }
    count = count * 10 + unsigned(c - '0');
  }
  if (!valid || count < minimum || count > maximum)
    throw std::runtime_error(std::string(flag) + " must be a decimal integer from " +
                             std::to_string(minimum) + " to " + std::to_string(maximum));
  return count;
}
static uint32_t little32(const uint8_t *p) {
  return uint32_t(p[0]) | (uint32_t(p[1]) << 8) | (uint32_t(p[2]) << 16) | (uint32_t(p[3]) << 24);
}
static NSData *readWad(NSString *path) {
  // Validate only the asset container. The original Doom engine interprets lumps.
  constexpr uint64_t limit = 64u * 1024u * 1024u;
  NSError *error = nil;
  NSDictionary *attributes = [[NSFileManager defaultManager] attributesOfItemAtPath:path
                                                                              error:&error];
  if (!attributes)
    throw std::runtime_error("Cannot inspect IWAD: " + boundedError(error));
  uint64_t bytes = [attributes fileSize];
  if (bytes < 12 || bytes > limit)
    throw std::runtime_error("IWAD must be between 12 bytes and 64 MiB");
  NSData *data = readData(path);
  const uint8_t *p = (const uint8_t *)data.bytes;
  if (data.length < 12 || data.length > limit)
    throw std::runtime_error("IWAD size changed while reading");
  if (memcmp(p, "IWAD", 4))
    throw std::runtime_error("Expected an IWAD base game file (header IWAD)");
  uint32_t count = little32(p + 4), directory = little32(p + 8);
  if (!count || directory < 12 || uint64_t(directory) + uint64_t(count) * 16 > data.length)
    throw std::runtime_error("Invalid IWAD directory bounds");
  for (uint32_t i = 0; i < count; ++i) {
    const uint8_t *entry = p + directory + uint64_t(i) * 16;
    uint32_t offset = little32(entry), size = little32(entry + 4);
    if (uint64_t(offset) + size > data.length)
      throw std::runtime_error("IWAD lump " + std::to_string(i) + " exceeds file bounds");
  }
  return data;
}
static void writeBytes(NSString *path, const void *p, size_t n) {
  NSData *d = [NSData dataWithBytes:p length:n];
  if (![d writeToFile:path atomically:YES])
    throw std::runtime_error("Cannot write output");
}
class Engine {
public:
  id<MTLDevice> device;
  id<MTLCommandQueue> queue;
  id<MTLLibrary> library;
  id<MTLComputePipelineState> pipeline;
  id<MTLBuffer> memory, mailbox, instance;
  uint32_t *control;
  uint32_t previousKeys = 0;
  double gpuMS = 0, tickGpuMS = 0, renderGpuMS = 0, frameGpuMS = 0;
  unsigned dispatches = 0, submissions = 0, renderCount = 0, batchedFrames = 0;
  bool batchFrames = true;
  struct Operation { uint32_t code, a = 0, b = 0; };
  NSUInteger workers = 32;
  Engine(NSString *shader, NSString *entry = @"doom_dispatch") {
    device = MTLCreateSystemDefaultDevice();
    if (!device)
      throw std::runtime_error("A Metal device is required");
    queue = [device newCommandQueue];
    MTLCompileOptions *options = [MTLCompileOptions new];
    options.languageVersion = MTLLanguageVersion3_2;
    options.mathMode = MTLMathModeSafe;
    NSError *error = nil;
    library = [device newLibraryWithSource:[NSString stringWithUTF8String:readText(shader).c_str()]
                                   options:options
                                     error:&error];
    if (!library)
      throw std::runtime_error(boundedError(error));
    NSMutableArray<id<MTLFunction>> *visible = [NSMutableArray array];
    for (NSString *name in library.functionNames) {
      id<MTLFunction> f = [library newFunctionWithName:name];
      if (f.functionType == MTLFunctionTypeVisible)
        [visible addObject:f];
    }
    MTLLinkedFunctions *links = [MTLLinkedFunctions new];
    links.functions = visible;
    MTLComputePipelineDescriptor *desc = [MTLComputePipelineDescriptor new];
    desc.computeFunction = [library newFunctionWithName:entry];
    if (!desc.computeFunction)
      throw std::runtime_error("Missing Metal entry " + std::string(entry.UTF8String));
    desc.linkedFunctions = links;
    desc.maxCallStackDepth = 32;
    pipeline = [device newComputePipelineStateWithDescriptor:desc
                                                     options:MTLPipelineOptionNone
                                                  reflection:nil
                                                       error:&error];
    if (!pipeline)
      throw std::runtime_error(boundedError(error));
    workers = pipeline.threadExecutionWidth;
    mailbox = [device newBufferWithLength:256 options:MTLResourceStorageModeShared];
    instance = [device newBufferWithLength:1024 * 1024 options:MTLResourceStorageModeShared];
    memset(mailbox.contents, 0, 256);
    memset(instance.contents, 0, instance.length);
    control = (uint32_t *)mailbox.contents;
    fprintf(stderr, "Metal device: %s; SIMD width %lu\n", device.name.UTF8String,
            (unsigned long)pipeline.threadExecutionWidth);
  }
  void submit(const std::vector<Operation> &operations) {
    if (operations.empty()) return;
    control[13] = 0;
    id<MTLCommandBuffer> cb = [queue commandBuffer];
    for (const Operation &operation : operations) {
      // Separate serial encoders keep tracked-resource dependencies explicit.
      // setBytes snapshots each operation; shared mailbox contents are outputs.
      id<MTLComputeCommandEncoder> enc = [cb computeCommandEncoder];
      [enc setComputePipelineState:pipeline];
      [enc setBuffer:memory offset:0 atIndex:0];
      [enc setBuffer:mailbox offset:0 atIndex:1];
      [enc setBuffer:instance offset:0 atIndex:2];
      [enc setBytes:&operation length:sizeof(operation) atIndex:3];
      [enc dispatchThreads:MTLSizeMake(workers, 1, 1)
          threadsPerThreadgroup:MTLSizeMake(workers, 1, 1)];
      [enc endEncoding];
    }
    [cb commit];
    [cb waitUntilCompleted];
    if (cb.status != MTLCommandBufferStatusCompleted)
      throw std::runtime_error(boundedError(cb.error));
    double elapsed = (cb.GPUEndTime - cb.GPUStartTime) * 1000;
    gpuMS += elapsed;
    ++submissions;
    dispatches += operations.size();
    if (operations.size() == 1 && operations[0].code == 1)
      tickGpuMS += elapsed;
    if (operations.back().code == 2) {
      ++renderCount;
      if (operations.size() == 1) renderGpuMS += elapsed;
      else { frameGpuMS += elapsed; ++batchedFrames; }
    }
    if (control[13])
      throw std::runtime_error("GPU engine trap " + std::to_string(control[13]));
  }
  uint32_t invoke(uint32_t operation, uint32_t a = 0, uint32_t b = 0) {
    submit({{operation, a, b}});
    return control[8];
  }
  void load(NSData *d, NSString *initial) {
    NSData *image = readData(initial);
    if (image.length != MemoryBytes) throw std::runtime_error("Expected a 96 MiB engine memory image");
    memory = [device newBufferWithLength:MAX(NSUInteger(MemoryBytes), image.length)
                                 options:MTLResourceStorageModeShared];
    memset(memory.contents, 0, memory.length);
    memcpy(memory.contents, image.bytes, image.length);
    invoke(5);
    uint32_t offset = invoke(3, (uint32_t)d.length);
    if (!offset || uint64_t(offset) + d.length > memory.length)
      throw std::runtime_error("WAD allocation failed");
    memcpy((uint8_t *)memory.contents + offset, d.bytes, d.length);
    uint32_t result = invoke(0, offset, (uint32_t)d.length);
    if (control[12] && uint64_t(control[11]) + control[12] <= memory.length)
      fwrite((uint8_t *)memory.contents + control[11], 1, MIN(control[12], 16384u), stderr);
    if (result)
      throw std::runtime_error("Doom initialization failed: " + std::to_string(result));
  }
  void appendInput(std::vector<Operation> &operations, uint32_t keys) {
    static const uint32_t keyCodes[] = {0xad, 0xaf, 0xac, 0xae, 0xa0, 0xa1, 0xa3, 0xa2, 13, 0xb6,
                                        49,   50,   51,   52,   53,   54,   55,   9,    27};
    uint32_t changed = keys ^ previousKeys;
    for (unsigned bit = 0; bit < 19; ++bit)
      if (changed & (1u << bit))
        operations.push_back({4, (keys >> bit) & 1, keyCodes[bit]});
    previousKeys = keys;
  }
  void step(uint32_t keys) {
    std::vector<Operation> operations;
    appendInput(operations, keys);
    for (const Operation &operation : operations) invoke(operation.code, operation.a, operation.b);
    invoke(1);
  }
  const uint8_t *frame(uint32_t keys, unsigned ticks = 1) {
    if (!batchFrames) {
      for (unsigned i = 0; i < ticks; ++i) step(keys);
      return pixels();
    }
    std::vector<Operation> operations;
    operations.reserve(19 + ticks + 1);
    appendInput(operations, keys);
    for (unsigned i = 0; i < ticks; ++i) operations.push_back({1});
    operations.push_back({2});
    submit(operations);
    return currentPixels();
  }
  const int32_t *diagnostics() {
    if (uint64_t(control[10]) + 64 > memory.length)
      throw std::runtime_error("Invalid diagnostics offset");
    return (const int32_t *)((uint8_t *)memory.contents + control[10]);
  }
  uint32_t stateHash() {
    uint32_t hash = 2166136261;
    const int32_t *d = diagnostics();
    for (unsigned i = 0; i < 16; ++i) {
      hash ^= (uint32_t)d[i];
      hash *= 16777619;
    }
    return hash;
  }
  const uint8_t *currentPixels() {
    if (uint64_t(control[9]) + Width * Height * 4 > memory.length)
      throw std::runtime_error("Invalid GPU framebuffer");
    return (uint8_t *)memory.contents + control[9];
  }
  const uint8_t *pixels() {
    invoke(2);
    return currentPixels();
  }
};

#include "persistent_host.h"

@interface DoomView : NSView
@property(nonatomic) uint32_t keys;
@property(nonatomic, strong) NSBitmapImageRep *bitmap;
@property(nonatomic, strong) CALayer *imageLayer;
@property(nonatomic, copy) NSString *loadingMessage;
@end
@implementation DoomView
@synthesize bitmap = _bitmap;
- (instancetype)initWithFrame:(NSRect)frame {
  self = [super initWithFrame:frame];
  if (self) {
    self.wantsLayer = YES;
    self.layer.backgroundColor = NSColor.blackColor.CGColor;
    self.imageLayer = [CALayer layer];
    self.imageLayer.magnificationFilter = kCAFilterNearest;
    self.imageLayer.minificationFilter = kCAFilterNearest;
    self.imageLayer.actions =
        @{@"contents" : NSNull.null, @"bounds" : NSNull.null, @"position" : NSNull.null};
    [self.layer addSublayer:self.imageLayer];
  }
  return self;
}
- (void)layout {
  [super layout];
  NSSize size = self.bounds.size;
  CGFloat scale = MIN(size.width / 320.0, size.height / 240.0);
  self.imageLayer.frame = CGRectMake((size.width - 320 * scale) / 2,
                                     (size.height - 240 * scale) / 2, 320 * scale, 240 * scale);
}
- (void)setBitmap:(NSBitmapImageRep *)bitmap {
  BOOL first = !_bitmap;
  _bitmap = bitmap;
  self.imageLayer.contents = bitmap ? (__bridge id)bitmap.CGImage : nil;
  self.needsLayout = YES;
  if (first)
    [self setNeedsDisplay:YES];
}
- (BOOL)acceptsFirstResponder {
  return YES;
}
- (void)drawRect:(NSRect)dirty {
  [[NSColor blackColor] setFill];
  NSRectFill(self.bounds);
  if (self.loadingMessage) {
    NSDictionary *style = @{
      NSFontAttributeName : [NSFont systemFontOfSize:22 weight:NSFontWeightMedium],
      NSForegroundColorAttributeName : NSColor.lightGrayColor
    };
    NSSize size = [self.loadingMessage sizeWithAttributes:style];
    [self.loadingMessage drawAtPoint:NSMakePoint((self.bounds.size.width - size.width) / 2,
                                                 (self.bounds.size.height - size.height) / 2)
                      withAttributes:style];
  }
}

- (uint32_t)mask:(unsigned short)c {
  switch (c) {
  case 13:
  case 126:
    return 1;
  case 1:
  case 125:
    return 2;
  case 0:
  case 123:
    return 4;
  case 2:
  case 124:
    return 8;
  case 12:
    return 16;
  case 14:
    return 32;
  case 49:
    return 64;
  case 36:
    return 128;
  case 6:
    return 256;
  case 56:
  case 60:
    return 512;
  case 18:
    return 1u << 10;
  case 19:
    return 1u << 11;
  case 20:
    return 1u << 12;
  case 21:
    return 1u << 13;
  case 23:
    return 1u << 14;
  case 22:
    return 1u << 15;
  case 26:
    return 1u << 16;
  case 48:
    return 1u << 17;
  case 46:
    return 1u << 18;
  default:
    return 0;
  }
}
- (void)keyDown:(NSEvent *)e {
  if (e.keyCode == 53) {
    [NSApp terminate:nil];
    return;
  }
  self.keys |= [self mask:e.keyCode];
}
- (void)keyUp:(NSEvent *)e {
  self.keys &= ~[self mask:e.keyCode];
}
- (void)flagsChanged:(NSEvent *)e {
  if (e.modifierFlags & NSEventModifierFlagShift)
    self.keys |= 512;
  else
    self.keys &= ~512;
}
@end
@interface AppDelegate : NSObject <NSApplicationDelegate, NSWindowDelegate>
@property(nonatomic, strong) NSWindow *window;
@property(nonatomic, strong) DoomView *view;
@property(nonatomic, strong) dispatch_source_t frameTimer;
@property(nonatomic) Engine *engine;
@property(nonatomic) PersistentEngine *liveEngine;
@property(nonatomic) BOOL usePersistent;
@property(nonatomic, copy) NSString *shaderPath;
@property(nonatomic, copy) NSString *initialPath;
@property(nonatomic, strong) NSData *wadData;
@property(nonatomic) std::chrono::steady_clock::time_point last;
@property(nonatomic) double accumulator;
@property(nonatomic, strong) dispatch_queue_t worker;
@property(nonatomic) BOOL inFlight;
@property(nonatomic) BOOL stopping;
@property(nonatomic) BOOL loading;
@property(nonatomic) double previousDisplay;
@property(nonatomic) double firstDisplay;
@property(nonatomic) unsigned frameSequence;
@property(nonatomic) double lastTitleUpdate;
@property(nonatomic) unsigned lastTitleFrame;
@end
@implementation AppDelegate
- (void)applicationDidFinishLaunching:(NSNotification *)n {
  fprintf(stderr, "GUI applicationDidFinishLaunching pid=%d\n", getpid());
  self.window =
      [[NSWindow alloc] initWithContentRect:NSMakeRect(0, 0, 960, 720)
                                  styleMask:NSWindowStyleMaskTitled | NSWindowStyleMaskClosable |
                                            NSWindowStyleMaskResizable
                                    backing:NSBackingStoreBuffered
                                      defer:NO];
  self.window.title = @"Metal Doom — Loading Doom…";
  self.window.delegate = self;
  self.view = [[DoomView alloc] initWithFrame:self.window.contentView.bounds];
  self.view.autoresizingMask = NSViewWidthSizable | NSViewHeightSizable;
  self.view.loadingMessage = @"Loading Doom… Compiling the GPU engine.";
  self.window.contentView = self.view;
  [self.window makeFirstResponder:self.view];
  [self.window center];
  [self.window makeKeyAndOrderFront:nil];
  [NSApp activateIgnoringOtherApps:YES];
  self.worker = dispatch_queue_create(
      "org.metaldoom.gpu", dispatch_queue_attr_make_with_qos_class(DISPATCH_QUEUE_SERIAL,
                                                                   QOS_CLASS_USER_INTERACTIVE, 0));
  if (!self.wadData) {
    self.view.loadingMessage = @"Select your Doom IWAD to begin.";
    NSOpenPanel *panel = [NSOpenPanel openPanel];
    panel.title = @"Select your Doom IWAD";
    panel.message =
        @"Choose doom1.wad or a compatible free IWAD. Game assets are supplied separately.";
    panel.canChooseFiles = YES;
    panel.canChooseDirectories = NO;
    panel.allowsMultipleSelection = NO;
    [panel beginSheetModalForWindow:self.window
                  completionHandler:^(NSModalResponse result) {
                    if (result != NSModalResponseOK) {
                      [NSApp terminate:nil];
                      return;
                    }
                    try {
                      self.wadData = readWad(panel.URL.path);
                      [self startEngine];
                    } catch (const std::exception &e) {
                      [self showError:[NSString stringWithUTF8String:e.what()]];
                    }
                  }];
  } else
    [self startEngine];
}
- (void)startEngine {
  self.loading = YES;
  self.view.loadingMessage = @"Loading Doom… Compiling the GPU engine.";
  [self.view setNeedsDisplay:YES];
  NSString *shader = self.shaderPath, *initial = self.initialPath;
  NSData *wad = self.wadData;
  dispatch_async(self.worker, ^{
    @autoreleasepool {
      Engine *created = nullptr;
      PersistentEngine *live = nullptr;
      try {
        fprintf(stderr, "GUI background GPU initialization started\n");
        if (self.usePersistent) {
          live = new PersistentEngine(shader);
          live->start(wad, initial);
          live->waitFrame();
          fprintf(stderr, "GUI persistent dispatch initialized; %s\n", live->log.c_str());
        } else {
          created = new Engine(shader);
          created->load(wad, initial);
        }
        dispatch_async(dispatch_get_main_queue(), ^{
          self.loading = NO;
          if (self.stopping) {
            if (live) {
              live->stop();
              fprintf(stderr, "GUI startup cancelled status=%ld dispatches=%u\n",
                      (long)live->command.status, live->dispatches);
            }
            delete created;
            delete live;
            return;
          }
          self.engine = created;
          self.liveEngine = live;
          self.wadData = nil;
          self.view.loadingMessage = nil;
          self.last = std::chrono::steady_clock::now();
          // One deadline per simulation frame avoids polling gaps after GPU completion.
          self.frameTimer = dispatch_source_create(
              DISPATCH_SOURCE_TYPE_TIMER, 0, DISPATCH_TIMER_STRICT, dispatch_get_main_queue());
          __weak AppDelegate *weakSelf = self;
          dispatch_source_set_event_handler(self.frameTimer, ^{
            [weakSelf update];
          });
          if (self.usePersistent)
            dispatch_source_set_timer(self.frameTimer, DISPATCH_TIME_NOW, NSEC_PER_SEC / 240, 100000);
          else [self scheduleNextFrame];
          dispatch_activate(self.frameTimer);
          fprintf(stderr, "GUI background GPU initialization complete\n");
        });
      } catch (const std::exception &e) {
        delete created;
        delete live;
        NSString *message = [NSString stringWithUTF8String:e.what()];
        dispatch_async(dispatch_get_main_queue(), ^{
          self.loading = NO;
          [self showError:message];
        });
      }
    }
  });
}
- (void)showError:(NSString *)message {
  self.inFlight = NO;
  if (self.stopping)
    return;
  self.stopping = YES;
  [self stopFrameTimer];
  if (self.liveEngine) self.liveEngine->requestStop();
  fprintf(stderr, "Metal Doom: %s\n", message.UTF8String);
  self.view.loadingMessage = @"Doom could not start.";
  [self.view setNeedsDisplay:YES];
  NSAlert *alert = [NSAlert new];
  alert.messageText = @"Metal Doom stopped";
  alert.informativeText = message;
  [alert beginSheetModalForWindow:self.window
                completionHandler:^(NSModalResponse result) {
                  [NSApp terminate:nil];
                }];
}
- (BOOL)applicationShouldTerminateAfterLastWindowClosed:(NSApplication *)s {
  return YES;
}
- (void)windowDidResignKey:(NSNotification *)n {
  self.view.keys = 0;
}
- (NSApplicationTerminateReply)applicationShouldTerminate:(NSApplication *)application {
  self.stopping = YES;
  [self stopFrameTimer];
  if (self.loading) {
    // Finish creation before releasing any buffers held by the pending command.
    // Its main-queue completion deletes the stopped instance before this reply.
    dispatch_async(self.worker, ^{
      dispatch_async(dispatch_get_main_queue(), ^{ [application replyToApplicationShouldTerminate:YES]; });
    });
    return NSTerminateLater;
  }
  if (self.liveEngine) {
    self.liveEngine->requestStop();
    dispatch_async(self.worker, ^{
      self.liveEngine->stop();
      fprintf(stderr, "GUI persistent dispatch stopped status=%ld frames=%u dispatches=%u\n",
              (long)self.liveEngine->command.status, self.liveEngine->sequence,
              self.liveEngine->dispatches);
      dispatch_async(dispatch_get_main_queue(), ^{ [application replyToApplicationShouldTerminate:YES]; });
    });
    return NSTerminateLater;
  }
  return NSTerminateNow;
}
- (void)stopFrameTimer {
  if (self.frameTimer) {
    dispatch_source_cancel(self.frameTimer);
    self.frameTimer = nil;
  }
}
- (void)scheduleNextFrame {
  if (self.stopping || self.inFlight || !self.frameTimer)
    return;
  // Include the elapsed GPU/display work when choosing the next 35 Hz tick deadline.
  double elapsed =
      std::chrono::duration<double>(std::chrono::steady_clock::now() - self.last).count();
  double delay = MAX(0.0, 1.0 / 35.0 - self.accumulator - elapsed);
  dispatch_source_set_timer(self.frameTimer,
                            dispatch_time(DISPATCH_TIME_NOW, (int64_t)(delay * NSEC_PER_SEC)),
                            DISPATCH_TIME_FOREVER, 100000);
}

- (void)displayFrame:(NSData *)frame state:(const int32_t *)state frameMS:(double)frameMS keys:(uint32_t)keys {
  int32_t gametic = state[0], x = state[4], y = state[5], angle = state[7];
  const uint8_t *p = (const uint8_t *)frame.bytes;
  NSBitmapImageRep *b =
      [[NSBitmapImageRep alloc] initWithBitmapDataPlanes:nullptr
                                              pixelsWide:Width
                                              pixelsHigh:Height
                                           bitsPerSample:8
                                         samplesPerPixel:4
                                                hasAlpha:YES
                                                isPlanar:NO
                                          colorSpaceName:NSDeviceRGBColorSpace
                                             bytesPerRow:Width * 4
                                            bitsPerPixel:32];
  for (unsigned i = 0; i < Width * Height; ++i) {
    b.bitmapData[i * 4] = p[i * 4 + 2];
    b.bitmapData[i * 4 + 1] = p[i * 4 + 1];
    b.bitmapData[i * 4 + 2] = p[i * 4];
    b.bitmapData[i * 4 + 3] = 255;
  }
  self.view.bitmap = b;
  double current =
      std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch())
          .count();
  double fps = self.previousDisplay ? 1.0 / (current - self.previousDisplay) : 0;
  self.previousDisplay = current;
  if (!self.frameSequence)
    self.firstDisplay = current;
  ++self.frameSequence;
  double averageFPS =
      self.frameSequence > 1 ? (self.frameSequence - 1) / (current - self.firstDisplay) : 0;
  if (self.frameSequence == 1 || self.frameSequence % 35 == 0) {
    fprintf(stderr, "GUI frame=%u fps=%.2f average_fps=%.2f mode=%s keys=%u gametic=%d "
                    "x=%d y=%d angle=%d",
            self.frameSequence, fps, averageFPS, self.usePersistent ? "persistent" : "dispatch",
            keys, gametic, x, y, angle);
    if (!self.usePersistent) fprintf(stderr, " gpu_frame_ms=%.3f", frameMS);
    fputc('\n', stderr);
  }
  // Updating the title every frame needlessly redraws AppKit chrome.
  if (!self.lastTitleUpdate || current - self.lastTitleUpdate >= 0.5) {
    double titleFPS = self.lastTitleUpdate ? (self.frameSequence - self.lastTitleFrame) /
                                                 (current - self.lastTitleUpdate)
                                           : fps;
    self.window.title = self.usePersistent
        ? [NSString stringWithFormat:@"Metal Doom · %.1f FPS · Persistent GPU · Esc quit", titleFPS]
        : [NSString stringWithFormat:@"Metal Doom · %.1f FPS · GPU %.1f ms/frame · Esc quit", titleFPS, frameMS];
    self.lastTitleUpdate = current;
    self.lastTitleFrame = self.frameSequence;
  }

}
- (void)updatePersistent {
  if (self.stopping || !self.liveEngine) return;
  try {
    self.liveEngine->input(self.view.keys);
    if (self.liveEngine->poll()) {
      NSData *frame = [NSData dataWithBytes:self.liveEngine->pixelsCopy() length:Width * Height * 4];
      [self displayFrame:frame state:self.liveEngine->stateCopy frameMS:0 keys:self.view.keys];
    }
    if (self.liveEngine->command.status >= MTLCommandBufferStatusCompleted)
      throw std::runtime_error("Persistent kernel stopped unexpectedly");
  } catch (const std::exception &e) {
    [self showError:[NSString stringWithUTF8String:e.what()]];
  }
}
- (void)update {
  if (self.usePersistent) { [self updatePersistent]; return; }
  auto now = std::chrono::steady_clock::now();
  self.accumulator += std::chrono::duration<double>(now - self.last).count();
  self.last = now;
  if (self.stopping || self.inFlight)
    return;
  unsigned count = MIN(8u, (unsigned)(self.accumulator * 35.0));
  if (!count) {
    [self scheduleNextFrame];
    return;
  }
  self.accumulator -= count / 35.0;
  self.inFlight = YES;
  // Input and scheduling remain on the main thread. Only this serial queue
  // accesses mutable engine memory after initialization.
  uint32_t keys = self.view.keys;
  Engine *engine = self.engine;
  dispatch_async(self.worker, ^{
    @autoreleasepool {
      try {
        double frameStart = engine->gpuMS;
        const uint8_t *pixels = engine->frame(keys, count);
        double frameMS = engine->gpuMS - frameStart;
        NSData *frame = [NSData dataWithBytes:pixels length:Width * Height * 4];
        int32_t gametic = engine->diagnostics()[0], x = engine->diagnostics()[4],
                y = engine->diagnostics()[5], angle = engine->diagnostics()[7];
        dispatch_async(dispatch_get_main_queue(), ^{
          self.inFlight = NO;
          if (self.stopping)
            return;
          int32_t state[16] = {};
          state[0] = gametic; state[4] = x; state[5] = y; state[7] = angle;
          [self displayFrame:frame state:state frameMS:frameMS keys:keys];
          [self scheduleNextFrame];
        });
      } catch (const std::exception &e) {
        NSString *message = [NSString stringWithUTF8String:e.what()];
        dispatch_async(dispatch_get_main_queue(), ^{
          [self showError:message];
        });
      }
    }
  });
}
@end
int main(int argc, char **argv) {
  @autoreleasepool {
    try {
      NSString *shader = @"build/doom.metal", *wad = nil, *initial = @"build/initial-memory.bin",
               *output = @"build/results";
      bool headless = false, scripted = false, renderEveryTick = false, profile = false,
           frameHashes = false, serialRender = false, batchFrames = true, persistent = false, dispatchMode = false, customShader = false;
      unsigned ticks = 350, samples = 12;
      for (int i = 1; i < argc; ++i) {
        std::string a = argv[i];
        auto value = [&]() {
          if (++i >= argc)
            throw std::runtime_error("Missing value after " + a);
          return [NSString stringWithUTF8String:argv[i]];
        };
        if (a == "--wad")
          wad = value();
        else if (a == "--shader") {
          shader = value(); customShader = true;
        }
        else if (a == "--initial-memory")
          initial = value();
        else if (a == "--output")
          output = value();
        else if (a == "--ticks")
          ticks = parseCount(value(), 0, 1000000, "--ticks");
        else if (a == "--samples")
          samples = parseCount(value(), 1, 1000000, "--samples");
        else if (a == "--persistent")
          persistent = true;
        else if (a == "--dispatch")
          dispatchMode = true;
        else if (a == "--headless")
          headless = true;
        else if (a == "--profile") {
          profile = true;
          headless = true;
        } else if (a == "--scripted")
          scripted = true;
        else if (a == "--render-every-tick")
          renderEveryTick = true;
        else if (a == "--no-batch")
          batchFrames = false;
        else if (a == "--serial-render")
          serialRender = true;
        else if (a == "--frame-hashes")
          frameHashes = true;
        else if (a == "--help") {
          puts("metal-doom --wad FILE [--headless --ticks N --scripted --render-every-tick "
               "--frame-hashes]\n"
               "           [--persistent | --dispatch] [--profile --samples N] [--serial-render] [--no-batch] [--output DIR]\n"
               "           [--shader FILE --initial-memory FILE]\n"
               "--ticks: simulation ticks (default 350); --samples: samples per profile phase "
               "(default 12)\n"
               "GUI defaults to --persistent; headless/profile defaults to --dispatch.");
          return 0;
        } else
          throw std::runtime_error("Unknown option " + a);
      }
      if (persistent && dispatchMode)
        throw std::runtime_error("Choose --persistent or --dispatch");
      if (persistent && (profile || !batchFrames))
        throw std::runtime_error("--profile and --no-batch use the dispatch backend");
      if (!batchFrames && !headless)
        throw std::runtime_error("--no-batch requires --headless or --profile");
      if (serialRender && !headless)
        throw std::runtime_error("--serial-render requires --headless or --profile");
      if (frameHashes && (!headless || profile || !renderEveryTick))
        throw std::runtime_error("--frame-hashes requires --headless --render-every-tick");
      if (!customShader && (persistent || (!headless && !dispatchMode))) shader = @"build/doom-persistent.metal";
      if (![[NSFileManager defaultManager] fileExistsAtPath:shader]) {
        NSString *candidate =
            [[NSBundle mainBundle].resourcePath stringByAppendingPathComponent:shader.lastPathComponent];
        if ([[NSFileManager defaultManager] fileExistsAtPath:candidate])
          shader = candidate;
      }
      if (![[NSFileManager defaultManager] fileExistsAtPath:initial]) {
        NSString *candidate = [[NSBundle mainBundle].resourcePath
            stringByAppendingPathComponent:@"initial-memory.bin"];
        if ([[NSFileManager defaultManager] fileExistsAtPath:candidate])
          initial = candidate;
      }
      if (!wad && getenv("DOOM_WAD"))
        wad = [NSString stringWithUTF8String:getenv("DOOM_WAD")];

      if (!wad && headless)
        throw std::runtime_error("Supply --wad /path/to/doom1.wad (game assets are not bundled)");
      NSData *wadData = wad ? readWad(wad) : nil;
      if (profile) {
        Engine engine(shader);
        engine.control[14] = serialRender;
        engine.batchFrames = batchFrames;
        engine.load(wadData, initial);
        // Profile repeated rendering of the same initialized game state; no simulation runs here.
        // Each measured command is isolated, and GPU timestamps exclude host-side waiting.
        [[NSFileManager defaultManager] createDirectoryAtPath:output
                                  withIntermediateDirectories:YES
                                                   attributes:nil
                                                        error:nil];
        if (!samples)
          throw std::runtime_error("--samples must be greater than zero");
        NSMutableDictionary *phases = [NSMutableDictionary dictionary];
        const uint32_t operations[] = {6, 7, 8};
        const char *names[] = {"palette", "world", "full_render"};
        for (unsigned phase = 0; phase < 3; ++phase) {
          engine.invoke(operations[phase]);
          double sum = 0, minimum = 1e30, maximum = 0;
          auto begin = std::chrono::steady_clock::now();
          for (unsigned i = 0; i < samples; ++i) {
            double before = engine.gpuMS;
            engine.invoke(operations[phase]);
            double ms = engine.gpuMS - before;
            sum += ms;
            minimum = MIN(minimum, ms);
            maximum = MAX(maximum, ms);
          }
          double wallMS =
              std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - begin)
                  .count();
          phases[[NSString stringWithUTF8String:names[phase]]] = @{
            @"mean_gpu_ms" : @(sum / samples),
            @"min_gpu_ms" : @(minimum),
            @"max_gpu_ms" : @(maximum),
            @"mean_wall_ms" : @(wallMS / samples)
          };
        }
        NSDictionary *result =
            @{@"device" : engine.device.name,
              @"samples" : @(samples),
              @"serial_render" : @(serialRender),
              @"phases" : phases};
        NSData *json = [NSJSONSerialization dataWithJSONObject:result
                                                       options:NSJSONWritingPrettyPrinted
                                                         error:nil];
        writeBytes([output stringByAppendingPathComponent:@"profile.json"], json.bytes,
                   json.length);
        fwrite(json.bytes, 1, json.length, stdout);
        putchar('\n');
        return 0;
      }
      if (headless && persistent)
        return persistentHeadless(shader, initial, wadData, output, ticks, scripted,
                                  renderEveryTick, frameHashes, serialRender);
      if (headless) {
        Engine engine(shader);
        engine.control[14] = serialRender;
        engine.batchFrames = batchFrames;
        engine.load(wadData, initial);
        [[NSFileManager defaultManager] createDirectoryAtPath:output
                                  withIntermediateDirectories:YES
                                                   attributes:nil
                                                        error:nil];
        std::ostringstream frameTrace;
        NSString *frameHashPath = [output stringByAppendingPathComponent:@"frame-hashes.csv"];
        if (frameHashes) {
          frameTrace << "tick,digest\n0," << sha256(engine.currentPixels(), Width * Height * 4)
                     << "\n";
        } else {
          [[NSFileManager defaultManager] removeItemAtPath:frameHashPath error:nil];
        }
        std::ostringstream trace;
        trace << "tick,hash,gametic,gamestate,health,armor,x,y,z,angle,kills,items,secrets,"
                 "rndindex,prndindex,leveltime,weapon,exited\n";
        double loopGPUStart = engine.gpuMS;
        auto start = std::chrono::steady_clock::now();
        const uint8_t *p = nullptr;
        for (unsigned i = 0; i < ticks; ++i) {
          uint32_t keys = scripted ? ((i < 140 ? 1u : 0u) | (i >= 140 && i < 210 ? 8u : 0u) |
                                      (i >= 210 && i < 280 ? 64u : 0u) | (i == 280 ? 128u : 0u))
                                   : 0;
          if (renderEveryTick) {
            p = engine.frame(keys);
            if (frameHashes)
              frameTrace << i + 1 << "," << sha256(p, Width * Height * 4) << "\n";
          } else engine.step(keys);
          trace << i + 1 << "," << engine.stateHash();
          for (unsigned j = 0; j < 16; ++j)
            trace << "," << engine.diagnostics()[j];
          trace << "\n";
        }
        if (!p)
          p = engine.pixels();
        double seconds =
            std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
        writeBytes([output stringByAppendingPathComponent:@"frame.rgba"], p, Width * Height * 4);
        if (frameHashes) {
          std::string hashes = frameTrace.str();
          writeBytes(frameHashPath, hashes.data(), hashes.size());
        }
        std::string csv = trace.str();
        writeBytes([output stringByAppendingPathComponent:@"ticks.csv"], csv.data(), csv.size());
        NSString *tickTime = engine.batchedFrames ? @"null" : [NSString stringWithFormat:@"%.6f", engine.tickGpuMS];
        NSString *renderTime = engine.batchedFrames ? @"null" : [NSString stringWithFormat:@"%.6f", engine.renderGpuMS];
        NSString *summary = [NSString
            stringWithFormat:
                @"{\"device\":\"%@\",\"ticks\":%u,\"seconds\":%.9f,\"gpu_ms\":%.6f,\"dispatches\":%"
                @"u,\"state_hash\":%u,\"width\":%u,\"height\":%u,\"tick_gpu_ms\":%@,\"render_gpu_"
                @"ms\":%@,\"frames\":%u,\"wall_fps\":%.6f,\"render_every_tick\":%@,\"serial_render\":%@,\"batch_frames\":%@,\"submissions\":%u,\"batched_frame_gpu_ms\":%.6f,\"loop_gpu_ms\":%.6f}\n",
                engine.device.name, ticks, seconds, engine.gpuMS, engine.dispatches,
                engine.stateHash(), Width, Height, tickTime, renderTime,
                engine.renderCount, engine.renderCount / seconds,
                renderEveryTick ? @"true" : @"false", serialRender ? @"true" : @"false", batchFrames ? @"true" : @"false", engine.submissions, engine.frameGpuMS, engine.gpuMS - loopGPUStart];
        writeBytes([output stringByAppendingPathComponent:@"summary.json"], summary.UTF8String,
                   strlen(summary.UTF8String));
        printf("%s", summary.UTF8String);
        return 0;
      }
      [NSApplication sharedApplication];
      [NSApp setActivationPolicy:NSApplicationActivationPolicyRegular];
      AppDelegate *delegate = [AppDelegate new];
      delegate.shaderPath = shader;
      delegate.initialPath = initial;
      delegate.wadData = wadData;
      delegate.usePersistent = !dispatchMode;
      [NSApp setDelegate:delegate];
      [NSApp run];
      if (delegate.worker)
        dispatch_sync(delegate.worker, ^{
                      });
      delete delegate.engine;
      delete delegate.liveEngine;
      return 0;
    } catch (const std::exception &e) {
      fprintf(stderr, "metal-doom: %s\n", e.what());
      return 1;
    }
  }
}
