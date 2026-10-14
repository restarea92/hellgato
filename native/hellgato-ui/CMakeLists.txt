cmake_minimum_required(VERSION 3.21)
project(HellgatoNativeUiCompatibility LANGUAGES CXX)
include(CTest)
add_subdirectory("${CMAKE_CURRENT_LIST_DIR}/../../host-integrations/streamdeck/windows/native/ui" "${CMAKE_CURRENT_BINARY_DIR}/canonical-ui")
