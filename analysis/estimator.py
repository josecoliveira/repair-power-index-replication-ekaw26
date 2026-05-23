import numpy as np
from scipy.stats import linregress

# Ignoring bctt and co-wheat as outliers.
N = np.array([     82,     91,    109,    127,     176,    242])
T = np.array([1656.16,2391.02,2834.72,6624.72,15546.08,57622.5])

log_N = np.log(N)
log_T = np.log(T)

# 1. Test Polynomial: Log-Log fit
slope_poly, intercept_poly, r_poly, _, _ = linregress(log_N, log_T)
r2_poly = r_poly**2

# 2. Test Exponential: Semi-Log fit
slope_exp, intercept_exp, r_exp, _, _ = linregress(N, log_T)
r2_exp = r_exp**2

print(f"Polynomial Fit (Log-Log): R² = {r2_poly:.4f}, Estimated Exponent (k) = {slope_poly:.2f}")
print(f"Exponential Fit (Semi-Log): R² = {r2_exp:.4f}, Estimated Base (b) = {np.exp(slope_exp):.2f}")

if r2_poly > r2_exp:
    print(f"Evidence favors Polynomial: O(N^{round(slope_poly)})")
else:
    print(f"Evidence favors Exponential: O({np.exp(slope_exp):.2f}^N)")