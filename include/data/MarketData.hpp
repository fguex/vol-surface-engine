#ifndef MARKETDATA_HPP
#define MARKETDATA_HPP

#include <string>
#include <vector>
#include "data/RateCurve.hpp"
#include "data/DivCurve.hpp"

namespace vse::data {

    struct OptionQuote {
        double K;
        double T;
        bool isCall;
        double bid;
        double ask;
        int volume;
        int openInterest;
        double mid() const noexcept { return 0.5 * (bid + ask); }
    };

    struct MarketData {
        double spot;
        RateCurve rates;
        DivCurve divs;
        std::vector<OptionQuote> calls;
        std::vector<OptionQuote> puts;

        static MarketData load(const std::string& folder, const std::string& today);
    };

} // namespace vse::data

#endif