#!/usr/bin/env bash
# Run only inside the isolated preproduction container. The short-lived registration token is a file.
set -euo pipefail
[[ "$(hostname)" == datacommune-preprod ]]
[[ "$(id -u)" == 0 ]]
token_file=/run/datacommune-preprod-runner-token
runner_dir=/opt/datacommune-preprod/runner
runner_user=datacommune-runner
id "$runner_user" >/dev/null
install -d -o "$runner_user" -g datacommune-preprod -m 0700 "$runner_dir"
cd "$runner_dir"
if [[ -f .runner ]]; then
  echo 'Runner already registered; keeping its identity.'
  ./svc.sh status
  exit 0
fi
test -s "$token_file"
trap 'rm -f -- "$token_file"' EXIT
archive=actions-runner-linux-x64-2.337.0.tar.gz
curl -fsSL --proto '=https' --tlsv1.2 \
  "https://github.com/actions/runner/releases/download/v2.337.0/$archive" -o "$archive"
printf '%s  %s\n' '70920811a4f8ad4328818682bca5c6469c1c942fab52448868071d0063816613' "$archive" | sha256sum -c -
tar xzf "$archive"
./bin/installdependencies.sh
chown -R "$runner_user":datacommune-preprod "$runner_dir"
# Do not enable shell tracing: the token must never enter the job journal.
runuser -u "$runner_user" -- ./config.sh --unattended --url https://github.com/DataCommune/datacommune \
  --token "$(<"$token_file")" --name ct277-datacommune-preprod \
  --labels datacommune-preprod --work _work
./svc.sh install "$runner_user"
./svc.sh start
rm -f -- "$token_file" "$archive"
echo 'Dedicated preproduction runner registered and started.'
