import React from 'react';
import { AdminDashboardLayout } from '@/components/AdminDashboardLayout';

export const AdminDeliveriesPage = () => (
  <AdminDashboardLayout title="پیشنهادات تحویل" subtitle="به کاربران و اجراهای اخیر">
    <div className="p-4 space-y-6">
      <h2 className="text-xl font-semibold">تحویل‌ها</h2>
      <p className="text-gray-600">لیست تحویل‌ها و وضعیت‌ها برای بررسی.</p>
      {/* TODO: Inject real delivery table / endpoints later */}
      <div className="border rounded-lg p-3 bg-gray-50">
        <p className="text-gray-500">بخش تحویل هنوز تکمیل نشده است.</p>
      </div>
    </div>
  </AdminDashboardLayout>
);