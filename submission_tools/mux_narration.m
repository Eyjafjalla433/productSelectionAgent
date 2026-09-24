#import <AVFoundation/AVFoundation.h>
#import <Foundation/Foundation.h>

// Build on macOS:
// clang -fobjc-arc -fblocks -framework Foundation -framework AVFoundation \
//   -framework CoreMedia submission_tools/mux_narration.m -o /tmp/mux_narration

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc != 4) {
            fprintf(stderr, "Usage: mux_narration VIDEO AUDIO OUTPUT\n");
            return 2;
        }

        NSURL *videoURL = [NSURL fileURLWithPath:[NSString stringWithUTF8String:argv[1]]];
        NSURL *audioURL = [NSURL fileURLWithPath:[NSString stringWithUTF8String:argv[2]]];
        NSURL *outputURL = [NSURL fileURLWithPath:[NSString stringWithUTF8String:argv[3]]];
        AVURLAsset *videoAsset = [AVURLAsset URLAssetWithURL:videoURL options:nil];
        AVURLAsset *audioAsset = [AVURLAsset URLAssetWithURL:audioURL options:nil];
        AVMutableComposition *composition = [AVMutableComposition composition];

        AVAssetTrack *sourceVideo = [[videoAsset tracksWithMediaType:AVMediaTypeVideo] firstObject];
        AVAssetTrack *sourceAudio = [[audioAsset tracksWithMediaType:AVMediaTypeAudio] firstObject];
        AVMutableCompositionTrack *videoTrack = [composition
            addMutableTrackWithMediaType:AVMediaTypeVideo
            preferredTrackID:kCMPersistentTrackID_Invalid];
        AVMutableCompositionTrack *audioTrack = [composition
            addMutableTrackWithMediaType:AVMediaTypeAudio
            preferredTrackID:kCMPersistentTrackID_Invalid];
        if (!sourceVideo || !sourceAudio || !videoTrack || !audioTrack) {
            fprintf(stderr, "Missing video or audio track.\n");
            return 1;
        }

        NSError *error = nil;
        if (![videoTrack insertTimeRange:CMTimeRangeMake(kCMTimeZero, videoAsset.duration)
                                 ofTrack:sourceVideo
                                  atTime:kCMTimeZero
                                   error:&error]) {
            fprintf(stderr, "Could not add video: %s\n", error.localizedDescription.UTF8String);
            return 1;
        }
        videoTrack.preferredTransform = sourceVideo.preferredTransform;

        CMTime narrationDuration = CMTIME_COMPARE_INLINE(audioAsset.duration, <, videoAsset.duration)
            ? audioAsset.duration : videoAsset.duration;
        if (![audioTrack insertTimeRange:CMTimeRangeMake(kCMTimeZero, narrationDuration)
                                 ofTrack:sourceAudio
                                  atTime:kCMTimeZero
                                   error:&error]) {
            fprintf(stderr, "Could not add audio: %s\n", error.localizedDescription.UTF8String);
            return 1;
        }

        AVAssetExportSession *exporter = [[AVAssetExportSession alloc]
            initWithAsset:composition
            presetName:AVAssetExportPresetHighestQuality];
        if (!exporter) {
            fprintf(stderr, "Could not create export session.\n");
            return 1;
        }
        exporter.outputURL = outputURL;
        exporter.outputFileType = AVFileTypeQuickTimeMovie;
        exporter.shouldOptimizeForNetworkUse = YES;

        dispatch_semaphore_t semaphore = dispatch_semaphore_create(0);
        [exporter exportAsynchronouslyWithCompletionHandler:^{
            dispatch_semaphore_signal(semaphore);
        }];
        dispatch_semaphore_wait(semaphore, DISPATCH_TIME_FOREVER);
        if (exporter.status != AVAssetExportSessionStatusCompleted) {
            fprintf(stderr, "Export failed: %s\n",
                    (exporter.error.localizedDescription ?: @"unknown error").UTF8String);
            return 1;
        }
        printf("%s\n", outputURL.path.UTF8String);
    }
    return 0;
}
