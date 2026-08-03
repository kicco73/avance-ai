#!/bin/sh


cd llama.cpp
export DYLD_LIBRARY_PATH="$(pwd)/build/bin:$DYLD_LIBRARY_PATH"
export VK_ICD_FILENAMES="/usr/local/share/vulkan/icd.d/MoltenVK_icd.json"
export LLM=Llama-3.2-3B-Instruct-Q4_K_M


#./build/bin/llama-server --list-devices
#exit
#curl -L -o llama-3.2-3b-instruct-q4_k_m.gguf \
# https://huggingface.co/pshebel/${LLM}-GGUF/resolve/main/${LLM}.gguf

./build/bin/llama-server -m ../models/${LLM}.gguf -c 6000 -b 512 -ub 128 -ngl 20 -np 1 --device Vulkan0 --reasoning-budget 0 --port 8081
