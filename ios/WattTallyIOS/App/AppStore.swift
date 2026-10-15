import SwiftUI

@MainActor final class AppStore: ObservableObject {
  @Published var credential: Credential?
  @Published var dashboard = Dashboard()
  @Published var error: String?
  @Published var busy = false
  let signIn = BrowserSignIn()
  init() {
    do { credential = try CredentialStore.read() } catch { self.error = error.localizedDescription }
  }
  var api: WattAPI? { credential.map { WattAPI(server: $0.server, token: $0.token) } }
  func login(_ address: String) async {
    guard
      let parts = URLComponents(string: address.trimmingCharacters(in: .whitespacesAndNewlines)),
      parts.scheme == "https", let host = parts.host, !host.isEmpty, parts.user == nil,
      parts.password == nil, parts.query == nil, parts.fragment == nil,
      ["", "/"].contains(parts.path), let url = parts.url
    else {
      error = "Enter an HTTPS server address without a path, such as https://watttally.malaber.de"
      return
    }
    busy = true
    defer { busy = false }
    do {
      let value = try await signIn.signIn(server: url)
      try CredentialStore.save(value)
      credential = value
      await refresh()
    } catch { self.error = error.localizedDescription }
  }
  func clearSession() throws {
    try CredentialStore.remove()
    credential = nil
    dashboard = Dashboard()
  }
  func refresh() async {
    guard let api else { return }
    do {
      dashboard = try WattAPI.decoder.decode(Dashboard.self, from: await api.data("dashboard"))
    } catch WattError.unauthorized {
      try? clearSession()
      self.error = "Session expired. Sign in again."
    } catch { self.error = error.localizedDescription }
  }
  func assign(_ session: Charge, car: Vehicle) async {
    guard let api else { return }
    busy = true
    defer { busy = false }
    do {
      var confirmation = ["vehicle_id": car.id]
      if session.end != nil { confirmation["energy_kwh"] = session.payload.energyKwh }
      _ = try await api.data(
        "sessions/" + session.id + "/assignment", method: "PATCH",
        body: JSONSerialization.data(withJSONObject: confirmation))
      await refresh()
    } catch { self.error = error.localizedDescription }
  }
  func report(car: String, month: String, timezone: String) async {
    guard let api else { return }
    busy = true
    defer { busy = false }
    do {
      _ = try await api.data(
        "reports", method: "POST",
        body: JSONSerialization.data(withJSONObject: [
          "vehicle_id": car, "month": month, "timezone": timezone,
        ]))
      await refresh()
    } catch { self.error = error.localizedDescription }
  }
  func download(_ report: Report, format: String) async -> URL? {
    guard let api else { return nil }
    do {
      let data = try await api.data("reports/" + report.id + "/" + format)
      let directory = FileManager.default.temporaryDirectory.appending(
        path: UUID().uuidString, directoryHint: .isDirectory)
      try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
      let file = directory.appending(path: "WattTally-" + report.month + "." + format)
      try data.write(to: file, options: [.atomic, .completeFileProtection])
      return file
    } catch {
      self.error = error.localizedDescription
      return nil
    }
  }
  func logout() async {
    busy = true
    defer { busy = false }
    do {
      if let api { _ = try await api.data("mobile/logout", method: "POST") }
      try clearSession()
    } catch WattError.unauthorized {
      do { try clearSession() } catch { self.error = error.localizedDescription }
    } catch { self.error = "Could not revoke your session. " + error.localizedDescription }
  }
}
