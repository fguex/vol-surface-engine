#ifndef DATEUTILS_HPP
#define DATEUTILS_HPP

#include <string>
#include <chrono>

namespace vse::data {

std::chrono::year_month_day parseDate(const std::string& s);
double yearsBetween(const std::string& from, const std::string& to);

} // namespace vse::data

#endif
