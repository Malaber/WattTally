import XCTest

@testable import WattTally

final class ContractTests: XCTestCase {
  func testServerDashboardDecodesAssignmentAndEnergy() throws {
    let data = Data(
      #"{"vehicles":[{"id":"car","name":"Tommy","plate":"NW FS 97E","policy":"actual"}],"instances":[],"sessions":[{"id":"charge","start":"2026-10-01T10:00:00+00:00","end":null,"vehicle_id":"car","assignment":"suggested","payload":{"energy_kwh":"3.125","grid_kwh":"1.125","solar_kwh":"2.000","flags":["missing_tariff"]}}],"reports":[{"id":"report","month":"2026-10","vehicle_id":"car","policy":"actual"}]}"#
        .utf8)
    let dashboard = try WattAPI.decoder.decode(Dashboard.self, from: data)
    XCTAssertEqual(dashboard.sessions[0].vehicleId, dashboard.vehicles[0].id)
    XCTAssertEqual(dashboard.sessions[0].payload.energyKwh, "3.125")
    XCTAssertFalse(dashboard.sessions[0].needsReview)
    XCTAssertNil(dashboard.sessions[0].end)
    XCTAssertEqual(dashboard.reports[0].vehicleId, "car")
  }
  func testEnergyConfirmationBecomesStaleWhenTotalChanges() throws {
    func charge(_ reviewed: String) throws -> Charge {
      let json = """
      {"id":"c","start":"2026-10-01T10:00:00Z","end":"2026-10-01T11:00:00Z","vehicle_id":"car","assignment":"confirmed","reviewed_energy_kwh":"\(reviewed)","payload":{"energy_kwh":"20","flags":["meter_gap"]}}
      """
      return try WattAPI.decoder.decode(Charge.self, from: Data(json.utf8))
    }
    XCTAssertFalse(try charge("20.0").needsReview)
    XCTAssertTrue(try charge("19").needsReview)
  }
  func testNativeTokenResponseUsesServerFieldNames() throws {
    let response = try WattAPI.decoder.decode(
      TokenResponse.self,
      from: Data(#"{"access_token":"opaque","user_id":"user","token_type":"bearer"}"#.utf8))
    XCTAssertEqual(response.accessToken, "opaque")
    XCTAssertEqual(response.userId, "user")
  }
}
