import SwiftUI

struct RootView: View {
  @EnvironmentObject private var store: AppStore
  @Environment(\.scenePhase) private var phase
  @State private var server = "https://watttally.malaber.de"
  var body: some View {
    Group {
      if store.credential == nil {
        NavigationStack {
          ScrollView {
            VStack(alignment: .leading, spacing: 24) {
              Image("Brand").resizable().scaledToFit().frame(width: 100, height: 100).clipShape(
                RoundedRectangle(cornerRadius: 24)
              ).accessibilityHidden(true)
              Text("Every charge\naccounted for.").font(.largeTitle.bold())
              Text("Your cars. Your homes. A clear record of every charging session.")
                .foregroundStyle(.secondary)
              VStack(alignment: .leading, spacing: 10) {
                Text("WattTally server").font(.headline)
                TextField("https://watttally.malaber.de", text: $server).textContentType(.URL)
                  .keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                  .textFieldStyle(.roundedBorder).accessibilityIdentifier("server")
                Button {
                  Task { await store.login(server) }
                } label: {
                  Label("Continue with passkey", systemImage: "person.badge.key.fill").frame(
                    maxWidth: .infinity
                  ).padding(8)
                }.buttonStyle(.borderedProminent).disabled(store.busy)
                Text(
                  "Sign in or create an account securely in your browser. New accounts need administrator approval."
                ).font(.footnote).foregroundStyle(.secondary)
              }
              Text("House first. Cars next.").font(.headline)
            }.padding(28)
          }.background(Color(red: 0.97, green: 0.94, blue: 0.90)).navigationTitle("WattTally")
        }
      } else {
        TabView {
          OverviewView().tabItem { Label("Overview", systemImage: "bolt.circle") }
          SessionsView().tabItem { Label("Sessions", systemImage: "car.side") }
          ReportsView().tabItem { Label("Exports", systemImage: "square.and.arrow.up") }
          NavigationStack {
            Form {
              Section("Your server") {
                Text(store.credential?.server.host ?? "")
                if let url = store.credential?.server {
                  Link("Manage homes, cars & passkeys", destination: url)
                }
              }
              Section {
                Text(
                  "Home Assistant access is read-only. Reports and assignments stay on your WattTally server."
                )
              }
              Section {
                Button("Sign out", role: .destructive) { Task { await store.logout() } }.disabled(
                  store.busy)
              }
            }.navigationTitle("Settings")
          }.tabItem { Label("Settings", systemImage: "gearshape") }
        }.task { await store.refresh() }
      }
    }.alert(
      "WattTally",
      isPresented: Binding(get: { store.error != nil }, set: { if !$0 { store.error = nil } })
    ) {
      Button("OK") { store.error = nil }
    } message: {
      Text(store.error ?? "")
    }
    .onChange(of: phase) { _, value in if value == .active { Task { await store.refresh() } } }
  }
}

struct OverviewView: View {
  @EnvironmentObject private var store: AppStore
  var body: some View {
    NavigationStack {
      List {
        Section {
          Label("Every charge accounted for", systemImage: "bolt.fill").font(.title2.bold())
          LabeledContent(
            "Recorded energy",
            value: energy(
              store.dashboard.sessions.reduce(0) { $0 + (Double($1.payload.energyKwh) ?? 0) }))
          LabeledContent(
            "Needs confirmation",
            value: "\(store.dashboard.sessions.filter(\.needsReview).count)")
        }
        Section("Your cars") {
          if store.dashboard.vehicles.isEmpty {
            Text("Add your Home Assistant connection and discover cars in server settings.")
              .foregroundStyle(.secondary)
          }
          ForEach(store.dashboard.vehicles) { car in
            VStack(alignment: .leading) {
              Label(car.name, systemImage: "car.side.fill").font(.headline)
              Text(car.plate.isEmpty ? "License plate missing" : car.plate)
              Text(car.policy == "employer" ? "Employer reimbursement" : "Actual charging cost")
                .font(.caption).foregroundStyle(.secondary)
            }
          }
        }
        Section("Your homes") {
          ForEach(store.dashboard.instances) { home in
            VStack(alignment: .leading) {
              Text(home.name)
              if let error = home.error {
                Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(.orange)
              }
            }
          }
        }
      }.navigationTitle("WattTally").refreshable { await store.refresh() }
    }
  }
}

func energy(_ value: Double) -> String {
  value.formatted(.number.precision(.fractionLength(2))) + " kWh"
}
func dateLabel(_ value: String) -> String {
  let parser = ISO8601DateFormatter()
  parser.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
  if let date = parser.date(from: value) {
    return date.formatted(date: .abbreviated, time: .shortened)
  }
  parser.formatOptions = [.withInternetDateTime]
  return parser.date(from: value)?.formatted(date: .abbreviated, time: .shortened) ?? value
}

struct SessionsView: View {
  @EnvironmentObject private var store: AppStore
  @State private var onlyReview = false
  var body: some View {
    NavigationStack {
      List {
        Toggle("Needs confirmation", isOn: $onlyReview)
        ForEach(store.dashboard.sessions.filter { ($0.end == nil || (Double($0.payload.energyKwh) ?? 0) > 0) && (!onlyReview || $0.needsReview) }) { charge in
          NavigationLink {
            SessionDetail(charge: charge)
          } label: {
            VStack(alignment: .leading, spacing: 6) {
              Text(
                store.dashboard.vehicles.first { $0.id == charge.vehicleId }?.name
                  ?? "Unassigned car"
              ).font(.headline)
              Text(dateLabel(charge.start)).font(.subheadline)
              HStack {
                Text(energy(Double(charge.payload.energyKwh) ?? 0))
                Spacer()
                Text(charge.end == nil ? "Charging" : charge.assignment).foregroundStyle(
                  charge.needsReview ? .orange : .secondary)
              }
              if charge.needsReview {
                Label("Check car and kWh", systemImage: "exclamationmark.triangle").font(
                  .caption
                ).foregroundStyle(.orange)
              }
            }.padding(.vertical, 4)
          }
        }
        if store.dashboard.sessions.isEmpty {
          Text("Charging cycles appear here automatically after your server collects them.")
            .foregroundStyle(.secondary)
        }
      }.navigationTitle("Sessions").refreshable { await store.refresh() }
    }
  }
}

struct SessionDetail: View {
  @EnvironmentObject private var store: AppStore
  let charge: Charge
  @State private var selected = ""
  @State private var confirm = false
  var current: Charge { store.dashboard.sessions.first { $0.id == charge.id } ?? charge }
  var body: some View {
    Form {
      Section("Charging cycle") {
        LabeledContent("Started", value: dateLabel(current.start))
        LabeledContent("Ended", value: current.end.map(dateLabel) ?? "Charging now")
        LabeledContent("Energy", value: energy(Double(current.payload.energyKwh) ?? 0))
        LabeledContent("Assignment", value: current.assignment)
      }
      if store.dashboard.vehicles.first(where: { $0.id == current.vehicleId })?.policy == "actual" {
        Section("Household first") {
          LabeledContent(
            "Grid", value: current.payload.gridKwh.map { energy(Double($0) ?? 0) } ?? "Unknown")
          LabeledContent(
            "Solar", value: current.payload.solarKwh.map { energy(Double($0) ?? 0) } ?? "Unknown")
        }
      }
      if !current.payload.flags.isEmpty {
        Section("History notes (accepted when you confirm)") {
          ForEach(current.payload.flags, id: \.self) {
            Text($0.replacingOccurrences(of: "_", with: " "))
          }
        }
      }
      Section("Confirm charge") {
        Picker("Car", selection: $selected) {
          Text("Choose car").tag("")
          ForEach(store.dashboard.vehicles) { Text($0.name + " · " + $0.plate).tag($0.id) }
        }
        Button(current.end == nil ? "Confirm car" : "Confirm charge") { confirm = true }.disabled(selected.isEmpty || store.busy || (current.end != nil && (Double(current.payload.energyKwh) ?? 0) <= 0))
        Text("Check that the car and kWh match your charge. Confirmation accepts this energy total even if meter history has gaps.").font(
          .footnote
        ).foregroundStyle(.secondary)
      }
    }.navigationTitle("Charging details").onAppear { selected = current.vehicleId ?? "" }
      .confirmationDialog(current.end == nil ? "Confirm the car currently charging?" : "Confirm this car and the displayed charged kWh?", isPresented: $confirm)
    {
      Button(current.end == nil ? "Confirm car" : "Confirm charge") {
        if let car = store.dashboard.vehicles.first(where: { $0.id == selected }) {
          Task { await store.assign(current, car: car) }
        }
      }
    }
  }
}

struct SharedFile: Identifiable {
  let id = UUID()
  let url: URL
}
struct ReportsView: View {
  @EnvironmentObject private var store: AppStore
  @State private var car = ""
  @State private var month = Date()
  @State private var timezone = "Europe/Berlin"
  @State private var file: SharedFile?
  var body: some View {
    NavigationStack {
      List {
        Section("Create report") {
          Picker("Car", selection: $car) {
            Text("Choose car").tag("")
            ForEach(store.dashboard.vehicles) { Text($0.name).tag($0.id) }
          }
          DatePicker("Month containing", selection: $month, displayedComponents: .date)
          TextField("Timezone", text: $timezone).textInputAutocapitalization(.never)
            .autocorrectionDisabled()
          Button("Create monthly report") {
            let formatter = DateFormatter()
            formatter.locale = Locale(identifier: "en_US_POSIX")
            formatter.calendar = Calendar(identifier: .gregorian)
            formatter.dateFormat = "yyyy-MM"
            Task {
              await store.report(car: car, month: formatter.string(from: month), timezone: timezone)
            }
          }.disabled(car.isEmpty || store.busy)
          Text(
            "Employer reports use the agreed rate for every kWh. Solar and household costs are omitted."
          ).font(.footnote).foregroundStyle(.secondary)
        }
        Section("Saved reports") {
          ForEach(store.dashboard.reports) { report in
            VStack(alignment: .leading, spacing: 10) {
              Text(
                (store.dashboard.vehicles.first { $0.id == report.vehicleId }?.name ?? "Car")
                  + " · " + report.month
              ).font(.headline)
              HStack {
                ForEach(["pdf", "xlsx", "csv"], id: \.self) { format in
                  Button(format.uppercased()) {
                    Task {
                      if let url = await store.download(report, format: format) {
                        file = SharedFile(url: url)
                      }
                    }
                  }.buttonStyle(.bordered)
                }
              }
            }
          }
          if store.dashboard.reports.isEmpty {
            Text("No saved reports yet.").foregroundStyle(.secondary)
          }
        }
      }.navigationTitle("Exports").refreshable { await store.refresh() }
        .sheet(item: $file) { value in
          NavigationStack {
            VStack(spacing: 24) {
              Image(systemName: "doc.text").font(.largeTitle)
              Text(value.url.lastPathComponent)
              ShareLink(item: value.url) {
                Label("Share or save report", systemImage: "square.and.arrow.up")
              }.buttonStyle(.borderedProminent)
            }.padding().navigationTitle("Report ready").toolbar { Button("Done") { file = nil } }
          }
        }
    }
  }
}
