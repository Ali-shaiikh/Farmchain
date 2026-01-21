const jwt = require('jsonwebtoken');
const User = require('../models/User');

const JWT_SECRET = process.env.JWT_SECRET || 'your-secret-key-change-in-production';

const generateToken = (userId, role) => {
  return jwt.sign({ userId, role }, JWT_SECRET, { expiresIn: '24h' });
};

// Infer which role login page to use based on the route being accessed
const inferRoleFromRequest = (req) => {
  const combined = `${req.baseUrl || ''}${req.path || ''}${req.originalUrl || ''}`.toLowerCase();
  if (combined.includes('/admin')) return 'admin';
  if (combined.includes('/seller')) return 'seller';
  return 'farmer';
};

const verifyToken = (req, res, next) => {
  const token = req.cookies.token || req.body?.token || req.query?.token || req.headers.authorization?.replace('Bearer ', '');
  const loginRole = inferRoleFromRequest(req);
  const loginPath = `/auth/login/${loginRole}`;
  
  if (!token) {
    console.error('[auth] missing token, redirecting to', loginPath, 'cookies seen:', req.headers.cookie || '(none)');
    return res.redirect(loginPath);
  }

  try {
    const decoded = jwt.verify(token, JWT_SECRET);
    req.user = decoded;
    next();
  } catch (error) {
    console.error('[auth] invalid token, redirecting to', loginPath, 'error:', error.message);
    res.clearCookie('token');
    return res.redirect(loginPath);
  }
};

const requireRole = (roles) => {
  return (req, res, next) => {
    const loginRole = inferRoleFromRequest(req);
    const loginPath = `/auth/login/${loginRole}`;

    if (!req.user) {
      return res.redirect(loginPath);
    }
    
    if (!roles.includes(req.user.role)) {
      res.clearCookie('token');
      return res.redirect(loginPath);
    }
    
    next();
  };
};

const requireFarmer = requireRole(['farmer']);
const requireSeller = requireRole(['seller']);
const requireAdmin = requireRole(['admin']);

module.exports = {
  generateToken,
  verifyToken,
  requireRole,
  requireFarmer,
  requireSeller,
  requireAdmin,
  JWT_SECRET
};
  