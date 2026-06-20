#include "data/MarketData.hpp"
#include <fstream>
#include <sstream>
#include <stdexcept>

namespace vse::data {

static std::vector<OptionQuote> loadOptions(const std::string& path, bool isCall) {
    std::ifstream file(path);
    if (!file.is_open())
        throw std::runtime_error("Cannot open: " + path);

    std::string line;
    std::getline(file, line); // skip header

    std::vector<OptionQuote> quotes;

    while (std::getline(file, line)) {
        try {
            std::istringstream ss(line);
            std::string token;

            std::getline(ss, token, ','); // expiration (skip)
            std::getline(ss, token, ','); double T = std::stod(token);
            std::getline(ss, token, ','); // daysToExpiry (skip)
            std::getline(ss, token, ','); // type (skip)
            std::getline(ss, token, ',');
            ExerciseType exercise = (token == "American") ? ExerciseType::American : ExerciseType::European;
            std::getline(ss, token, ','); double K = std::stod(token);
            std::getline(ss, token, ','); double bid = std::stod(token);
            std::getline(ss, token, ','); double ask = std::stod(token);
            std::getline(ss, token, ','); // lastPrice (skip)
            std::getline(ss, token, ','); int volume = static_cast<int>(std::stod(token));
            std::getline(ss, token, ','); int oi = static_cast<int>(std::stod(token));

            if (bid > 0.0 && ask > 0.0) {
                quotes.push_back(OptionQuote{.K=K, .T=T, .isCall=isCall, .exercise=exercise, .bid=bid, .ask=ask, .volume=volume, .openInterest=oi});
            }
        } catch (...) {
            continue; // skip malformed lines
        }
    }

    return quotes;
}

MarketData MarketData::load(const std::string& folder, const std::string& today) {
    // 1. Spot
    std::ifstream spotFile(folder + "/aapl_spot.csv");
    if (!spotFile.is_open())
        throw std::runtime_error("Cannot open spot file");
    std::string line;
    std::getline(spotFile, line); // header
    std::getline(spotFile, line);
    double spot = std::stod(line.substr(line.find(',') + 1));

    // 2. Rates
    auto rates = RateCurve::fromCSV(folder + "/treasury_rates.csv");

    // 3. Divs
    auto divs = DivCurve::fromCSV(folder + "/aapl_dividends.csv", today);

    // 4. Options
    auto calls = loadOptions(folder + "/aapl_calls.csv", true);
    auto puts = loadOptions(folder + "/aapl_puts.csv", false);

    return MarketData{spot, std::move(rates), std::move(divs),
                      std::move(calls), std::move(puts)};
}

} // namespace vse::data
