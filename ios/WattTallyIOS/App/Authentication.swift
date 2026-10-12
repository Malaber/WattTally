import AuthenticationServices
import CryptoKit
import Security
import SwiftUI

struct Credential: Codable {
  let server: URL
  let token: String
  let userID: String
}

struct TokenResponse: Decodable {
  let accessToken: String
  let userId: String
}

@MainActor
enum CredentialStore {
  private static let query: [String: Any] = [
    kSecClass as String: kSecClassGenericPassword,
    kSecAttrService as String: "de.malaber.watttally",
    kSecAttrAccount as String: "session",
  ]

  static func read() throws -> Credential? {
    var query = query
    query[kSecReturnData as String] = true
    var result: CFTypeRef?
    let status = SecItemCopyMatching(query as CFDictionary, &result)
    if status == errSecItemNotFound { return nil }
    guard status == errSecSuccess, let data = result as? Data else {
      throw WattError.server(
        "Could not unlock your saved account. Unlock your device and try again.")
    }
    return try JSONDecoder().decode(Credential.self, from: data)
  }

  static func save(_ credential: Credential) throws {
    let data = try JSONEncoder().encode(credential)
    let status = SecItemUpdate(
      query as CFDictionary, [kSecValueData as String: data] as CFDictionary)
    if status == errSecItemNotFound {
      var item = query
      item[kSecValueData as String] = data
      item[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
      guard SecItemAdd(item as CFDictionary, nil) == errSecSuccess else {
        throw WattError.server("Could not securely save your sign-in.")
      }
    } else if status != errSecSuccess {
      throw WattError.server("Could not securely save your sign-in.")
    }
  }

  static func remove() throws {
    let status = SecItemDelete(query as CFDictionary)
    guard status == errSecSuccess || status == errSecItemNotFound else {
      throw WattError.server("Could not remove your sign-in.")
    }
  }
}

@MainActor
final class BrowserSignIn: NSObject, ASWebAuthenticationPresentationContextProviding {
  private var session: ASWebAuthenticationSession?

  func signIn(server: URL) async throws -> Credential {
    let verifier = try random()
    let state = try random()
    let challenge = Data(SHA256.hash(data: Data(verifier.utf8))).base64URL
    var url = URLComponents(
      url: server.appending(path: "api/v1/auth/mobile/authorize"), resolvingAgainstBaseURL: false)!
    url.queryItems = [
      URLQueryItem(name: "state", value: state),
      URLQueryItem(name: "code_challenge", value: challenge),
    ]
    let callback: URL = try await withCheckedThrowingContinuation { continuation in
      let session = ASWebAuthenticationSession(
        url: url.url!, callbackURLScheme: "de.malaber.watttally"
      ) {
        callback, error in
        if let callback {
          continuation.resume(returning: callback)
        } else {
          continuation.resume(throwing: error ?? WattError.server("Sign-in did not finish."))
        }
      }
      session.presentationContextProvider = self
      session.prefersEphemeralWebBrowserSession = true
      self.session = session
      if !session.start() {
        continuation.resume(throwing: WattError.server("Could not open sign-in."))
      }
    }
    session = nil
    let values = URLComponents(url: callback, resolvingAgainstBaseURL: false)?.queryItems ?? []
    guard callback.scheme == "de.malaber.watttally", callback.host == "auth",
      values.first(where: { $0.name == "state" })?.value == state,
      let code = values.first(where: { $0.name == "code" })?.value
    else {
      throw WattError.server("Sign-in could not be verified. Try again.")
    }
    let api = WattAPI(server: server, token: "")
    let body = try JSONSerialization.data(withJSONObject: ["code": code, "code_verifier": verifier])
    let response = try WattAPI.decoder.decode(
      TokenResponse.self, from: await api.data("v1/auth/mobile/token", method: "POST", body: body))
    return Credential(server: server, token: response.accessToken, userID: response.userId)
  }

  func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
    UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
      .flatMap(\.windows).first(where: \.isKeyWindow) ?? ASPresentationAnchor()
  }

  private func random() throws -> String {
    var bytes = [UInt8](repeating: 0, count: 32)
    guard SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes) == errSecSuccess else {
      throw WattError.server("Could not start a secure sign-in. Try again.")
    }
    return Data(bytes).base64URL
  }
}

extension Data {
  fileprivate var base64URL: String {
    base64EncodedString().replacingOccurrences(of: "+", with: "-")
      .replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "=", with: "")
  }
}
