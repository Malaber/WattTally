import XCTest

final class WattTallyUITests: XCTestCase {
  func testWelcomeScreen() {
    let app = XCUIApplication()
    app.launch()
    XCTAssertTrue(app.buttons["Continue with passkey"].waitForExistence(timeout: 10))
    XCTAssertEqual(app.textFields["server"].value as? String, "https://watttally.malaber.de")
    XCTAssertTrue(app.staticTexts["House first. Cars next."].exists)
  }
}
