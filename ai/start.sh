#!/bin/sh


cd llama.cpp
export DYLD_LIBRARY_PATH="$(pwd)/build/bin:$DYLD_LIBRARY_PATH"
export VK_ICD_FILENAMES="/usr/local/share/vulkan/icd.d/MoltenVK_icd.json"
export LLM=Llama-3.2-3B-Instruct-Q4_K_M

#./build/bin/llama-server --list-devices
#exit

./build/bin/llama-server \
  -m ../models/${LLM}.gguf \
  -c 6000 \
  -b 512 \
  -ub 512 \
  -ngl 28 \
  -np 1 \
  --device Vulkan0 \
  --port 8081
