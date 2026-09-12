#include "data/MarketData.hpp"
#include <fstream>
#include <sstream>
#include <stdexcept>

namespace vse::data {

// Colonnes quotes.csv produites par python/data/export.py :
// K, T, isCall, exerciseType, bid, ask, volume, openInterest
static void loadQuotes(const std::string& path,
                       std::vector<OptionQuote>& calls,
                       std::vector<OptionQuote>& puts)
{
    std::ifstream file(path);
    if (!file.is_open())
        throw std::runtime_error("Cannot open: " + path);

    std::string line;
    std::getline(file, line); // header

    while (std::getline(file, line)) {
        if (line.empty()) continue;
        try {
            std::istringstream ss(line);
            std::string token;

            std::getline(ss, token, ','); double K            = std::stod(token);
            std::getline(ss, token, ','); double T            = std::stod(token);
            std::getline(ss, token, ','); bool   isCall       = (std::stoi(token) == 1);
            std::getline(ss, token, ',');
            ExerciseType exercise = (token == "American") ? ExerciseType::American
                                                          : ExerciseType::European;
            std::getline(ss, token, ','); double bid          = std::stod(token);
            std::getline(ss, token, ','); double ask          = std::stod(token);
            std::getline(ss, token, ','); int    volume       = std::stoi(token);
            std::getline(ss, token, ','); int    oi           = std::stoi(token);

            OptionQuote q{.K=K, .T=T, .isCall=isCall, .exercise=exercise,
                          .bid=bid, .ask=ask, .volume=volume, .openInterest=oi};
            (isCall ? calls : puts).push_back(q);
        } catch (...) {
            continue;
        }
    }
}

MarketData MarketData::load(const std::string& folder, const std::string& today)
{
    (void)today; // reserve pour filtrage par date si necessaire

    // Spot : optionnel, lit data/csv/spot.txt si present
    double spot = 0.0;
    {
        std::ifstream sf(folder + "/spot.txt");
        if (sf.is_open()) sf >> spot;
    }

    // Taux : optionnel — taux plat 0% si rates.csv absent
    RateCurve rates = [&]() -> RateCurve {
        std::ifstream rf(folder + "/rates.csv");
        if (rf.is_open()) return RateCurve::fromCSV(folder + "/rates.csv");
        return RateCurve{{0.0, 30.0}, {0.0, 0.0}}; // flat 0%
    }();

    // Dividendes : optionnel — aucun dividende si divs.csv absent
    DivCurve divs = [&]() -> DivCurve {
        std::ifstream df(folder + "/divs.csv");
        if (df.is_open()) return DivCurve::fromCSV(folder + "/divs.csv", today);
        return DivCurve{};
    }();

    // Options
    std::vector<OptionQuote> calls, puts;
    loadQuotes(folder + "/quotes.csv", calls, puts);

    return MarketData{spot, std::move(rates), std::move(divs),
                      std::move(calls), std::move(puts)};
}

} // namespace vse::data
