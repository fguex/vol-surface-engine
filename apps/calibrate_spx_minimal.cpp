#include "calibration/ImpliedVol.hpp"
#include "calibration/SSVI.hpp"
#include "data/MarketData.hpp"
#include "pricing/blackscholes.hpp"

#include <Eigen/Dense>

#include <algorithm>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <map>
#include <sstream>
#include <string>
#include <utility>
#include <variant>
#include <vector>

namespace {

struct IVPoint {
    double k;
    double T;
    double iv;
};

struct SliceDiag {
    double T;
    int n_points;
    double rmse;
    double max_abs_error;
};

struct NodeDiag {
    double T;
    double k;
    double iv_market;
    double iv_model;
    double residual;
};

struct FitDiag {
    double rmse_total;
    double max_abs_error;
    double eta_ratio;
    std::vector<SliceDiag> slices;
    std::vector<NodeDiag> nodes;
};

std::string jsonQuote(const std::string& s) {
    std::ostringstream out;
    out << '"';
    for (char c : s) {
        switch (c) {
            case '\\': out << "\\\\"; break;
            case '"':  out << "\\\""; break;
            case '\n': out << "\\n"; break;
            case '\r': out << "\\r"; break;
            case '\t': out << "\\t"; break;
            default:   out << c; break;
        }
    }
    out << '"';
    return out.str();
}

double interpolateSlice(const std::vector<std::pair<double, double>>& pts, double k) {
    if (pts.empty()) return -1.0;
    if (k < pts.front().first || k > pts.back().first) return -1.0;

    auto it = std::lower_bound(
        pts.begin(), pts.end(), std::make_pair(k, -std::numeric_limits<double>::infinity())
    );
    if (it == pts.begin()) return it->second;
    if (it == pts.end()) return pts.back().second;

    auto hi = it;
    auto lo = std::prev(it);
    const double w = (k - lo->first) / (hi->first - lo->first);
    return lo->second + w * (hi->second - lo->second);
}

double phiSqSupFactorLocal(double gamma) noexcept {
    if (gamma > 0.5) return std::numeric_limits<double>::infinity();
    const double a = 1.0 - 2.0 * gamma;
    if (a == 0.0) return 1.0;
    return std::pow(a, a) / std::pow(1.0 + a, 1.0 + a);
}

double etaMaxBoundLocal(double gamma, double rho) noexcept {
    const double ar = 1.0 + std::abs(rho);
    return std::min(4.0 / ar, 2.0 / std::sqrt(phiSqSupFactorLocal(gamma) * ar));
}

FitDiag computeDiagnostics(
    std::span<const double> k_grid,
    std::span<const double> T_grid,
    const Eigen::MatrixXd& iv_market,
    const vse::calibration::SSVIParams& p
) {
    FitDiag out{0.0, 0.0, 0.0, {}, {}};
    out.slices.reserve(T_grid.size());
    out.nodes.reserve(T_grid.size() * k_grid.size());

    double sse_total = 0.0;
    int n_total = 0;

    for (std::size_t i = 0; i < T_grid.size(); ++i) {
        double sse_slice = 0.0;
        double max_abs_slice = 0.0;

        for (std::size_t j = 0; j < k_grid.size(); ++j) {
            const double model_iv = vse::calibration::impliedVolSSVI(k_grid[j], T_grid[i], p);
            const double err = model_iv - iv_market(i, j);
            const double abs_err = std::abs(err);
            sse_total += err * err;
            sse_slice += err * err;
            out.max_abs_error = std::max(out.max_abs_error, abs_err);
            max_abs_slice = std::max(max_abs_slice, abs_err);
            out.nodes.push_back(NodeDiag{
                .T = T_grid[i],
                .k = k_grid[j],
                .iv_market = iv_market(i, j),
                .iv_model = model_iv,
                .residual = err,
            });
            ++n_total;
        }

        out.slices.push_back(SliceDiag{
            .T = T_grid[i],
            .n_points = static_cast<int>(k_grid.size()),
            .rmse = std::sqrt(sse_slice / static_cast<double>(k_grid.size())),
            .max_abs_error = max_abs_slice,
        });
    }

    out.rmse_total = std::sqrt(sse_total / static_cast<double>(n_total));
    out.eta_ratio = p.eta / etaMaxBoundLocal(p.gamma, p.rho);
    return out;
}

void writeJsonArtifact(
    const std::string& path,
    const std::string& folder,
    const std::string& date,
    double spot,
    double r,
    double q,
    std::size_t valid_iv_points,
    double k_lo,
    double k_hi,
    const vse::calibration::SSVIParams& params,
    const FitDiag& diag
) {
    const auto output_path = std::filesystem::path(path);
    if (output_path.has_parent_path()) {
        std::filesystem::create_directories(output_path.parent_path());
    }
    std::ofstream out(output_path);
    if (!out.is_open()) {
        throw std::runtime_error("Cannot open JSON output path: " + path);
    }

    out << "{\n";
    out << "  \"metadata\": {\n";
    out << "    \"folder\": " << jsonQuote(folder) << ",\n";
    out << "    \"date\": " << jsonQuote(date) << ",\n";
    out << "    \"spot\": " << spot << ",\n";
    out << "    \"r\": " << r << ",\n";
    out << "    \"q\": " << q << ",\n";
    out << "    \"valid_iv_points\": " << valid_iv_points << ",\n";
    out << "    \"retained_maturities\": " << diag.slices.size() << ",\n";
    out << "    \"k_lo\": " << k_lo << ",\n";
    out << "    \"k_hi\": " << k_hi << "\n";
    out << "  },\n";

    out << "  \"params\": {\n";
    out << "    \"rho\": " << params.rho << ",\n";
    out << "    \"eta\": " << params.eta << ",\n";
    out << "    \"gamma\": " << params.gamma << ",\n";
    out << "    \"nu\": " << params.nu << "\n";
    out << "  },\n";

    out << "  \"diagnostics\": {\n";
    out << "    \"rmse_total\": " << diag.rmse_total << ",\n";
    out << "    \"max_abs_error\": " << diag.max_abs_error << ",\n";
    out << "    \"eta_ratio\": " << diag.eta_ratio << "\n";
    out << "  },\n";

    out << "  \"slices\": [\n";
    for (std::size_t i = 0; i < diag.slices.size(); ++i) {
        const auto& s = diag.slices[i];
        out << "    {\"T\": " << s.T
            << ", \"n_points\": " << s.n_points
            << ", \"rmse\": " << s.rmse
            << ", \"max_abs_error\": " << s.max_abs_error << "}";
        out << (i + 1 == diag.slices.size() ? "\n" : ",\n");
    }
    out << "  ],\n";

    out << "  \"nodes\": [\n";
    for (std::size_t i = 0; i < diag.nodes.size(); ++i) {
        const auto& n = diag.nodes[i];
        out << "    {\"T\": " << n.T
            << ", \"k\": " << n.k
            << ", \"iv_market\": " << n.iv_market
            << ", \"iv_model\": " << n.iv_model
            << ", \"residual\": " << n.residual << "}";
        out << (i + 1 == diag.nodes.size() ? "\n" : ",\n");
    }
    out << "  ]\n";
    out << "}\n";
}

} // namespace

int main(int argc, char** argv) {
    std::string folder = "data/csv/SPX";
    std::string today = "2026-09-12";
    std::string json_out;

    for (int i = 1; i < argc; ++i) {
        const std::string arg = argv[i];
        if (arg == "--json-out" && i + 1 < argc) {
            json_out = argv[++i];
        } else if (folder == "data/csv/SPX") {
            folder = arg;
        } else if (today == "2026-09-12") {
            today = arg;
        }
    }

    const auto md = vse::data::MarketData::load(folder, today);
    const double S0 = md.spot > 0.0 ? md.spot : 7656.98;
    const double r = 0.0;
    const double q = 0.0;

    std::vector<IVPoint> pts;
    auto add_iv = [&](const vse::data::OptionQuote& quote, bool is_call) {
        const double mid = quote.mid();
        if (mid <= 0.0 || quote.T <= 0.0) return;

        const double F = S0 * std::exp((r - q) * quote.T);
        const double k = std::log(quote.K / F);
        if (std::abs(k) > 0.50) return;

        vse::pricing::BSParams bp{S0, quote.K, quote.T, r, q, 0.20};
        const auto type = is_call
            ? vse::calibration::OptionType::Call
            : vse::calibration::OptionType::Put;

        const auto res = vse::calibration::impliedVol(mid, bp, type, 1e-4, 5.0, 1e-7, 200);
        if (std::holds_alternative<vse::calibration::IVResult>(res)) {
            pts.push_back({k, quote.T, std::get<vse::calibration::IVResult>(res).sigma});
        }
    };

    for (const auto& qte : md.calls) add_iv(qte, true);
    for (const auto& qte : md.puts)  add_iv(qte, false);

    if (pts.empty()) {
        std::cerr << "No valid implied-vol points.\n";
        return 1;
    }

    std::map<double, std::vector<std::pair<double, double>>> by_T;
    for (const auto& p : pts) by_T[p.T].push_back({p.k, p.iv});

    std::vector<double> T_grid;
    std::vector<std::vector<std::pair<double, double>>> slices;
    for (auto& [T, slice] : by_T) {
        if (slice.size() < 8) continue;
        std::sort(slice.begin(), slice.end());
        if (T < 0.08) continue;
        if (slice.front().first > -0.05) continue;
        if (slice.back().first  <  0.05) continue;
        T_grid.push_back(T);
        slices.push_back(slice);
    }

    if (T_grid.empty()) {
        std::cerr << "No bilateral slices after filtering.\n";
        return 1;
    }

    double k_lo = -1.0;
    double k_hi =  1.0;
    for (const auto& slice : slices) {
        k_lo = std::max(k_lo, slice.front().first);
        k_hi = std::min(k_hi, slice.back().first);
    }
    if (k_lo >= k_hi) {
        std::cerr << "Empty common k-range.\n";
        return 1;
    }

    constexpr int NK = 11;
    std::vector<double> k_grid(NK);
    for (int j = 0; j < NK; ++j) {
        k_grid[j] = k_lo + j * (k_hi - k_lo) / static_cast<double>(NK - 1);
    }

    std::vector<double> T_ok;
    std::vector<Eigen::VectorXd> rows;
    for (std::size_t i = 0; i < slices.size(); ++i) {
        Eigen::VectorXd row(NK);
        bool ok = true;
        for (int j = 0; j < NK; ++j) {
            const double iv = interpolateSlice(slices[i], k_grid[j]);
            if (iv < 0.0) {
                ok = false;
                break;
            }
            row[j] = iv;
        }
        if (ok) {
            T_ok.push_back(T_grid[i]);
            rows.push_back(row);
        }
    }

    if (rows.empty()) {
        std::cerr << "Interpolation produced no full slices.\n";
        return 1;
    }

    Eigen::MatrixXd iv_ok(rows.size(), NK);
    for (std::size_t i = 0; i < rows.size(); ++i) iv_ok.row(i) = rows[i];

    const auto result = vse::calibration::calibrateSSVI(k_grid, T_ok, iv_ok);
    if (!std::holds_alternative<vse::calibration::SSVIParams>(result)) {
        std::cerr << "SSVI calibration failed.\n";
        return 1;
    }

    const auto params = std::get<vse::calibration::SSVIParams>(result);
    const auto diag = computeDiagnostics(k_grid, T_ok, iv_ok, params);

    std::cout << "Minimal European SSVI runner\n";
    std::cout << "folder=" << folder << " date=" << today << "\n";
    std::cout << "spot=" << S0 << " r=" << r << " q=" << q << "\n\n";

    std::cout << "valid_iv_points=" << pts.size() << "\n";
    std::cout << "retained_maturities=" << T_ok.size() << "\n";
    std::cout << "common_k_range=[" << k_lo << ", " << k_hi << "]\n\n";

    std::cout << "params\n";
    std::cout << "  rho=" << params.rho << "\n";
    std::cout << "  eta=" << params.eta << "\n";
    std::cout << "  gamma=" << params.gamma << "\n";
    std::cout << "  nu=" << params.nu << "\n";
    std::cout << "  eta_ratio=" << diag.eta_ratio << "\n\n";

    std::cout << "fit_quality\n";
    std::cout << "  rmse_total=" << diag.rmse_total << "\n";
    std::cout << "  max_abs_error=" << diag.max_abs_error << "\n\n";

    std::cout << "slice_diagnostics\n";
    for (const auto& s : diag.slices) {
        std::cout << "  T=" << s.T
                  << " n=" << s.n_points
                  << " rmse=" << s.rmse
                  << " max_abs=" << s.max_abs_error << "\n";
    }

    if (!json_out.empty()) {
        writeJsonArtifact(json_out, folder, today, S0, r, q, pts.size(), k_lo, k_hi, params, diag);
        std::cout << "\njson_artifact=" << json_out << "\n";
    }

    return 0;
}
