import React from 'react';
import { AdminDashboardLayout } from '@/components/AdminDashboardLayout';

export const AdminShopPage = () => (
  <AdminDashboardLayout title="أ thiếuर्नی" subtitle="تعداد خریدها و محبوبیت اجزاء">
    <div className="p-4 space-y-6">
      <h2 className="text-xl font-semibold">آمار ایمنی خریدها</h2>
      <p className="text-gray-600">نمودار تراکنش‌های روزانه، درصد افزایش فروش و لیست محبوب‌ترین اجزاء.</p>
      {/* Placeholder for actual shop analytics */}
      <div className="border rounded-lg p-4 bg-gray-50">
        <p className="text-gray-500">داده‌های popularity در حال آماده‌سازی.</p>
      </div>
    </div>
  </AdminDashboardLayout>
);