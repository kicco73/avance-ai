#!/bin/sh

cd llama.cpp || exit 1

export DYLD_LIBRARY_PATH="$(pwd)/build/bin:$DYLD_LIBRARY_PATH"
export VK_ICD_FILENAMES="/usr/local/share/vulkan/icd.d/MoltenVK_icd.json"

export LLM="bartowski/Llama-3.2-3B-Instruct-GGUF"
export LLM_QUANTISATION="Q5_K_M"

# ./build/bin/llama-server --list-devices
# exit

hf download "$LLM" \
    --include "*${LLM_QUANTISATION}.gguf"

if [ $? -ne 0 ]; then
    echo "Error downloading model"
    exit 1
fi

echo "Model available."

exec ./build/bin/llama-server \
    -hf "${LLM}:${LLM_QUANTISATION}" \
    -c 3072 \
    -b 512 \
    -ub 256 \
    -ngl 24 \
    -t 6 \
    -np 1 \
    --device Vulkan0 \
    --reasoning-budget 0 \
    --port 8081