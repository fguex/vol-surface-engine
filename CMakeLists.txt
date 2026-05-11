cmake_minimum_required(VERSION 3.20)
project(VolSurfaceEngine VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

add_compile_options(
  -Wall -Wextra -Wpedantic
  $<$<CONFIG:Release>:-O3>
  $<$<CONFIG:Debug>:-g>
  $<$<CONFIG:Debug>:-O0>
)

find_package(Eigen3 5.0 REQUIRED NO_MODULE)
find_package(NLopt REQUIRED)

add_library(vol_surface_engine
    src/pricing/blackscholes.cpp
    src/calibration/ImpliedVol.cpp
    src/calibration/SSVI.cpp
)

target_include_directories(vol_surface_engine PUBLIC
    ${CMAKE_SOURCE_DIR}/include
)

target_link_libraries(vol_surface_engine PUBLIC
    Eigen3::Eigen
    NLopt::nlopt
)

add_executable(main src/main.cpp)
target_link_libraries(main PRIVATE vol_surface_engine)
