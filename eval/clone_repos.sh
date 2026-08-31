#!/usr/bin/env bash
# Clone the six CEK-8066 top-pick repos (read-only classification targets).
set -euo pipefail
cd "$(dirname "$0")/repos" 2>/dev/null || { mkdir -p "$(dirname "$0")/repos"; cd "$(dirname "$0")/repos"; }

clone() {
  local url="$1" name="$2"
  if [ -d "$name" ]; then echo "already cloned: $name"; else git clone --depth 1 "$url" "$name"; fi
}

clone https://github.com/allgpt-co/QuickVoice.git quickvoice
clone https://github.com/kirklandsig/AIReceptionist.git aireceptionist
clone https://github.com/livekit-examples/outbound-caller-python.git outbound-caller-python
clone https://github.com/pipecat-ai/pipecat-examples.git pipecat-examples
clone https://github.com/NVIDIA/voice-agent-examples.git nvidia-voice-agent-examples
clone https://github.com/steinathan/telephony-server.git telephony-server
echo "done"
