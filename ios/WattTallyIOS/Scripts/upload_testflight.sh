#!/usr/bin/env bash
set -euo pipefail

package_dir=$(cd "$(dirname "$0")/.." && pwd)
marketing_version=${1:-}
build_number=${2:-}

if [[ ! "$marketing_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "Usage: $0 <marketing-version> <build-number>" >&2
  exit 2
fi
if [[ ! "$build_number" =~ ^[1-9][0-9]*$ ]]; then
  echo "Build number must be a positive integer." >&2
  exit 2
fi

temp_base=${TMPDIR:-/tmp}
release_dir=$(mktemp -d "${temp_base%/}/watttally-testflight.XXXXXX")
archive_path="$release_dir/WattTally-$marketing_version-$build_number.xcarchive"
export_path="$release_dir/export"

cd "$package_dir"
xcodegen generate

env PATH=/usr/bin:/bin:/usr/sbin:/sbin /usr/bin/xcodebuild \
  -project WattTallyApp.xcodeproj \
  -scheme WattTally \
  -configuration Release \
  -destination 'generic/platform=iOS' \
  -archivePath "$archive_path" \
  DEVELOPMENT_TEAM=VWKG94374J \
  PRODUCT_BUNDLE_IDENTIFIER=de.malaber.watttally \
  MARKETING_VERSION="$marketing_version" \
  CURRENT_PROJECT_VERSION="$build_number" \
  CODE_SIGN_STYLE=Automatic \
  -allowProvisioningUpdates \
  archive

env PATH=/usr/bin:/bin:/usr/sbin:/sbin /usr/bin/xcodebuild \
  -exportArchive \
  -archivePath "$archive_path" \
  -exportPath "$export_path" \
  -exportOptionsPlist "$package_dir/ExportOptions.TestFlight.plist" \
  -allowProvisioningUpdates

echo "TestFlight upload accepted. Archive: $archive_path"
