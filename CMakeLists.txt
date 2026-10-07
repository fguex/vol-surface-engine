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
  src/calibration/LocalVol.cpp
  src/calibration/FitDiagnostics.cpp
  src/data/RateCurve.cpp
  src/data/DivCurve.cpp
  src/data/DateUtils.cpp
  src/data/MarketData.cpp
    src/pde/Grid.cpp
    src/pde/Tridiag.cpp
    src/pde/PDEPricer.cpp
    src/opt/NelderMead.cpp
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

# A source file is only turned into an executable once it defines main().
# Stubs (comment-only files) are skipped and picked up automatically later.
function(vse_has_main src out_var)
  file(STRINGS ${src} hits REGEX "^[ \t]*int[ \t]+main[ \t]*\\(")
  if(hits)
    set(${out_var} TRUE PARENT_SCOPE)
  else()
    set(${out_var} FALSE PARENT_SCOPE)
  endif()
endfunction()

# ---- apps/ : one executable per workflow ------------------------------------
file(GLOB VSE_APP_SOURCES CONFIGURE_DEPENDS ${CMAKE_SOURCE_DIR}/apps/*.cpp)
foreach(src IN LISTS VSE_APP_SOURCES)
  vse_has_main(${src} has_main)
  if(has_main)
    get_filename_component(name ${src} NAME_WE)
    add_executable(${name} ${src})
    target_link_libraries(${name} PRIVATE vol_surface_engine)
  endif()
endforeach()

# ---- tests/ : run with `ctest --test-dir build --output-on-failure` ---------
enable_testing()
file(GLOB VSE_TEST_SOURCES CONFIGURE_DEPENDS ${CMAKE_SOURCE_DIR}/tests/test_*.cpp)
foreach(src IN LISTS VSE_TEST_SOURCES)
  vse_has_main(${src} has_main)
  if(has_main)
    get_filename_component(name ${src} NAME_WE)
    add_executable(${name} ${src})
    target_link_libraries(${name} PRIVATE vol_surface_engine)
    # Release defines NDEBUG, which compiles assert() out: keep asserts live in tests.
    target_compile_options(${name} PRIVATE -UNDEBUG)
    add_test(NAME ${name} COMMAND ${name} WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
  endif()
endforeach()
