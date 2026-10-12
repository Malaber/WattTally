import CoreGraphics
import Foundation
import ImageIO
import UniformTypeIdentifiers

// The same vector geometry as design/mark.svg, rasterized without an alpha channel.
let target = CommandLine.arguments[1]
let context = CGContext(
  data: nil, width: 1024, height: 1024, bitsPerComponent: 8, bytesPerRow: 4096,
  space: CGColorSpace(name: CGColorSpace.sRGB)!, bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue)!
context.setFillColor(red: 109.0 / 255, green: 79.0 / 255, blue: 58.0 / 255, alpha: 1)
context.fill(CGRect(x: 0, y: 0, width: 1024, height: 1024))
context.setFillColor(red: 1, green: 247.0 / 255, blue: 233.0 / 255, alpha: 1)
let paths: [[(Double, Double)]] = [
  [
    (145, 302), (229, 302), (283, 602), (370, 420), (436, 420), (489, 602), (555, 302), (637, 302),
    (527, 744), (455, 744), (395, 546), (305, 744), (233, 744),
  ],
  [
    (658, 280), (887, 280), (867, 360), (790, 360), (735, 501), (828, 501), (666, 785), (706, 577),
    (632, 577), (713, 360), (638, 360),
  ],
]
for points in paths {
  context.beginPath()
  context.move(to: CGPoint(x: points[0].0, y: 1024 - points[0].1))
  for point in points.dropFirst() { context.addLine(to: CGPoint(x: point.0, y: 1024 - point.1)) }
  context.closePath()
  context.fillPath()
}
let destination = CGImageDestinationCreateWithURL(
  URL(fileURLWithPath: target) as CFURL, UTType.png.identifier as CFString, 1, nil)!
CGImageDestinationAddImage(destination, context.makeImage()!, nil)
precondition(CGImageDestinationFinalize(destination))
