import SwiftUI

@main struct WattTallyApp: App {
  @StateObject private var store = AppStore()
  var body: some Scene {
    WindowGroup {
      RootView().environmentObject(store).tint(Color(red: 0.43, green: 0.31, blue: 0.23))
    }
  }
}
