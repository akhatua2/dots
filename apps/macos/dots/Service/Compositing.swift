import CoreImage
import CoreImage.CIFilterBuiltins
import Foundation
import ImageIO
import UniformTypeIdentifiers

/// Putting an edit back into your full-size photo, on this Mac.
///
/// A pool edits a small copy of the photo (about a megapixel), and an edit model redraws the whole of it, so
/// what comes back is smaller than the photo and slightly different everywhere. This finds where the edit
/// actually changed the copy, and takes the edit only there, scaled up into the original; everywhere else the
/// photo keeps its own pixels, at its own size. A change over most of the picture (relighting, a new style)
/// was meant to be everywhere, so it is taken whole.
enum Compositing {
    struct Tuning {
        /// How different a spot must look, 0 to 1, to count as changed (after blurring away the redraw's noise)
        var threshold: Float = 0.08
        /// How far the changed region grows past what changed, as a share of the picture's longer side
        var grow: Double = 0.03
        /// Past this share of the picture changed, the edit was meant to be everywhere
        var largestShare: Double = 0.5
    }

    private static let context = CIContext(options: [.useSoftwareRenderer: false])

    /// The edit, put into `original` (the full-size photo) where it changed `sent` (the copy the pool edited).
    /// PNG at the original's size; nil if a picture can't be read.
    static func putBack(edited: Data, sent: Data, into original: URL, tuning: Tuning = Tuning()) -> Data? {
        guard let full = CIImage(contentsOf: original, options: [.applyOrientationProperty: true]),
              let copy = CIImage(data: sent, options: [.applyOrientationProperty: true]),
              let edit = CIImage(data: edited) else { return nil }
        let size = full.extent.size
        let editFull = scaled(edit, to: size)
        // Where the edit changed the copy, compared at the copy's size, where both are the same picture
        let editSmall = scaled(edit, to: copy.extent.size)
        let soften = max(1, copy.extent.width / 256)
        let difference = blurred(editSmall, soften).applyingFilter("CIDifferenceBlendMode",
                                                                    parameters: [kCIInputBackgroundImageKey: blurred(copy, soften)])
        let brightest = difference.applyingFilter("CIMaximumComponent")
        let changed = brightest.applyingFilter("CIColorThreshold", parameters: ["inputThreshold": tuning.threshold])
        let share = averageBrightness(changed)
        if share > tuning.largestShare { return png(editFull) }
        if share == 0 { return png(full) }
        let reach = max(2, copy.extent.width * tuning.grow)
        let region = changed
            .applyingFilter("CIMorphologyMinimum", parameters: [kCIInputRadiusKey: 1])  // drop specks
            .applyingFilter("CIMorphologyMaximum", parameters: [kCIInputRadiusKey: reach])
        let mask = scaled(blurred(region, reach / 3), to: size)
        let out = editFull.applyingFilter("CIBlendWithMask", parameters: [kCIInputBackgroundImageKey: full,
                                                                          kCIInputMaskImageKey: mask])
        return png(out.cropped(to: full.extent))
    }

    private static func scaled(_ image: CIImage, to size: CGSize) -> CIImage {
        let moved = image.transformed(by: CGAffineTransform(translationX: -image.extent.minX, y: -image.extent.minY))
        return moved.transformed(by: CGAffineTransform(scaleX: size.width / image.extent.width,
                                                       y: size.height / image.extent.height))
            .cropped(to: CGRect(origin: .zero, size: size))
    }

    private static func blurred(_ image: CIImage, _ radius: CGFloat) -> CIImage {
        image.clampedToExtent().applyingGaussianBlur(sigma: Double(radius)).cropped(to: image.extent)
    }

    /// The share of the picture that is white, for a black-and-white picture
    private static func averageBrightness(_ image: CIImage) -> Double {
        let average = image.applyingFilter("CIAreaAverage", parameters: [kCIInputExtentKey: CIVector(cgRect: image.extent)])
        var pixel = [UInt8](repeating: 0, count: 4)
        context.render(average, toBitmap: &pixel, rowBytes: 4, bounds: CGRect(x: 0, y: 0, width: 1, height: 1),
                       format: .RGBA8, colorSpace: nil)
        return Double(pixel[0]) / 255
    }

    private static func png(_ image: CIImage) -> Data? {
        context.pngRepresentation(of: image, format: .RGBA8, colorSpace: CGColorSpace(name: CGColorSpace.sRGB)!)
    }
}
