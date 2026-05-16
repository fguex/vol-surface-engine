#include "data/DateUtils.hpp"
#include <sstream>

namespace vse::data {

std::chrono::year_month_day parseDate(const std::string& s) {
    int y, m, d;
    char dash;
    std::istringstream ss(s);
    ss >> y >> dash >> m >> dash >> d;
    return std::chrono::year_month_day{
        std::chrono::year{y},
        std::chrono::month{unsigned(m)},
        std::chrono::day{unsigned(d)}
    };
}

double yearsBetween(const std::string& from, const std::string& to) {
    auto d1 = std::chrono::sys_days{parseDate(from)};
    auto d2 = std::chrono::sys_days{parseDate(to)};
    auto days = (d2 - d1).count();
    return static_cast<double>(days) / 365.25;
}

} // namespace vse::data
