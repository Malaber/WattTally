import Foundation

enum WattError: LocalizedError {
  case server(String)
  case unauthorized
  var errorDescription: String? {
    if case .server(let message) = self { return message }
    return "Session expired. Sign in again."
  }
}

struct Vehicle: Decodable, Identifiable { let id, name, plate, policy: String }
struct Home: Decodable, Identifiable {
  let id, name: String
  let error: String?
}
struct Charge: Decodable, Identifiable {
  let id, start, assignment: String
  let end, vehicleId, reviewedEnergyKwh: String?
  let payload: Energy
  var needsReview: Bool {
    guard end != nil, (Double(payload.energyKwh) ?? 0) > 0 else { return false }
    return assignment != "confirmed" || reviewedEnergyKwh.flatMap(Double.init) != Double(payload.energyKwh)
  }
}
struct Energy: Decodable {
  let energyKwh: String
  let gridKwh, solarKwh: String?
  let flags: [String]
}
struct Report: Decodable, Identifiable { let id, month, vehicleId, policy: String }
struct Dashboard: Decodable {
  var vehicles: [Vehicle] = []
  var instances: [Home] = []
  var sessions: [Charge] = []
  var reports: [Report] = []
}

final class WattAPI: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
  let server: URL
  let token: String
  init(server: URL, token: String) {
    self.server = server
    self.token = token
  }
  static var decoder: JSONDecoder {
    let value = JSONDecoder()
    value.keyDecodingStrategy = .convertFromSnakeCase
    return value
  }
  func urlSession(
    _ session: URLSession, task: URLSessionTask,
    willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest,
    completionHandler: @escaping (URLRequest?) -> Void
  ) {
    completionHandler(nil)  // Never forward a bearer credential to redirects.
  }
  func data(_ path: String, method: String = "GET", body: Data? = nil) async throws -> Data {
    var request = URLRequest(url: server.appending(path: "api/" + path))
    request.httpMethod = method
    request.httpBody = body
    request.setValue("application/json", forHTTPHeaderField: "Content-Type")
    if !token.isEmpty { request.setValue("Bearer " + token, forHTTPHeaderField: "Authorization") }
    let config = URLSessionConfiguration.ephemeral
    config.httpCookieStorage = nil
    config.urlCache = nil
    config.timeoutIntervalForRequest = 30
    let session = URLSession(configuration: config, delegate: self, delegateQueue: nil)
    defer { session.finishTasksAndInvalidate() }
    let (data, response) = try await session.data(for: request)
    if (response as? HTTPURLResponse)?.statusCode == 401 { throw WattError.unauthorized }
    guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
      let detail = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
      throw WattError.server(
        detail?["detail"] as? String
          ?? "Could not reach WattTally. Check your server or sign in again.")
    }
    return data
  }
}
