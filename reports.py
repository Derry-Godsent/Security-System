from reportlab.lib.pagesizes import letter, A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from io import BytesIO
import csv
from datetime import datetime, date, timedelta
from sqlalchemy import func, and_

class ReportGenerator:
    def __init__(self, db, Guard, Attendance, Location, Company):
        self.db = db
        self.Guard = Guard
        self.Attendance = Attendance
        self.Location = Location
        self.Company = Company
        self.styles = getSampleStyleSheet()
        self.setup_custom_styles()
    
    def setup_custom_styles(self):
        # PANOS branded styles
        self.styles.add(ParagraphStyle(
            name='PANOSTitle',
            parent=self.styles['Title'],
            fontSize=18,
            spaceAfter=30,
            textColor=colors.HexColor('#141414'),
            alignment=TA_CENTER
        ))
        
        self.styles.add(ParagraphStyle(
            name='PANOSHeading',
            parent=self.styles['Heading2'],
            fontSize=14,
            spaceBefore=20,
            spaceAfter=10,
            textColor=colors.HexColor('#008000'),
        ))

    def generate_daily_attendance_report(self, report_date=None, format='pdf'):
        if not report_date:
            report_date = date.today()
        
        # Get attendance data
        attendance_data = self._get_daily_attendance_data(report_date)
        
        if format == 'pdf':
            return self._generate_daily_pdf(attendance_data, report_date)
        elif format == 'csv':
            return self._generate_daily_csv(attendance_data, report_date)

    def generate_weekly_attendance_report(self, start_date=None, format='pdf'):
        if not start_date:
            start_date = date.today() - timedelta(days=7)
        end_date = start_date + timedelta(days=6)
        
        # Get weekly data
        weekly_data = self._get_weekly_attendance_data(start_date, end_date)
        
        if format == 'pdf':
            return self._generate_weekly_pdf(weekly_data, start_date, end_date)
        elif format == 'csv':
            return self._generate_weekly_csv(weekly_data, start_date, end_date)

    def generate_guard_performance_report(self, days=30, format='pdf'):
        end_date = date.today()
        start_date = end_date - timedelta(days=days)
        
        performance_data = self._get_guard_performance_data(start_date, end_date)
        
        if format == 'pdf':
            return self._generate_performance_pdf(performance_data, start_date, end_date)
        elif format == 'csv':
            return self._generate_performance_csv(performance_data, start_date, end_date)

    def generate_location_analysis_report(self, days=30, format='pdf'):
        end_date = date.today()
        start_date = end_date - timedelta(days=days)
        
        location_data = self._get_location_analysis_data(start_date, end_date)
        
        if format == 'pdf':
            return self._generate_location_pdf(location_data, start_date, end_date)
        elif format == 'csv':
            return self._generate_location_csv(location_data, start_date, end_date)

    def _get_daily_attendance_data(self, report_date):
        # Get all attendance records for the date
        attendance_records = self.db.session.query(
            self.Attendance, self.Guard, self.Location, self.Company
        ).join(
            self.Guard, self.Attendance.guard_id == self.Guard.id
        ).join(
            self.Location, self.Guard.location_id == self.Location.id
        ).join(
            self.Company, self.Location.company_id == self.Company.id
        ).filter(
            self.Attendance.date == report_date
        ).all()
        
        # Calculate statistics
        total_guards = self.Guard.query.count()
        marked_guards = len(attendance_records)
        present_count = len([r for r in attendance_records if r[0].status == 'present'])
        absent_count = len([r for r in attendance_records if r[0].status == 'absent'])
        off_count = len([r for r in attendance_records if r[0].status == 'off'])
        leave_count = len([r for r in attendance_records if r[0].status == 'leave'])
        
        return {
            'date': report_date,
            'records': attendance_records,
            'statistics': {
                'total_guards': total_guards,
                'marked_guards': marked_guards,
                'present': present_count,
                'absent': absent_count,
                'off': off_count,
                'leave': leave_count,
                'attendance_rate': (present_count / marked_guards * 100) if marked_guards > 0 else 0
            }
        }

    def _get_weekly_attendance_data(self, start_date, end_date):
        # Get attendance for each day of the week
        weekly_stats = []
        current_date = start_date
        
        while current_date <= end_date:
            daily_data = self._get_daily_attendance_data(current_date)
            weekly_stats.append(daily_data)
            current_date += timedelta(days=1)
        
        return weekly_stats

    def _get_guard_performance_data(self, start_date, end_date):
        # Calculate attendance rate for each guard
        guards = self.Guard.query.all()
        performance_data = []
        
        for guard in guards:
            total_days = self.db.session.query(func.count(self.Attendance.id)).filter(
                and_(
                    self.Attendance.guard_id == guard.id,
                    self.Attendance.date >= start_date,
                    self.Attendance.date <= end_date
                )
            ).scalar()
            
            present_days = self.db.session.query(func.count(self.Attendance.id)).filter(
                and_(
                    self.Attendance.guard_id == guard.id,
                    self.Attendance.date >= start_date,
                    self.Attendance.date <= end_date,
                    self.Attendance.status == 'present'
                )
            ).scalar()
            
            attendance_rate = (present_days / total_days * 100) if total_days > 0 else 0
            
            performance_data.append({
                'guard': guard,
                'total_days': total_days,
                'present_days': present_days,
                'attendance_rate': attendance_rate
            })
        
        # Sort by attendance rate
        performance_data.sort(key=lambda x: x['attendance_rate'], reverse=True)
        return performance_data

    def _get_location_analysis_data(self, start_date, end_date):
        locations = self.Location.query.filter_by(is_accessible=True).all()
        location_data = []
        
        for location in locations:
            # Get all guards at this location
            guards = self.Guard.query.filter_by(location_id=location.id).all()
            
            total_possible_attendance = 0
            actual_attendance = 0
            
            for guard in guards:
                days_count = self.db.session.query(func.count(self.Attendance.id)).filter(
                    and_(
                        self.Attendance.guard_id == guard.id,
                        self.Attendance.date >= start_date,
                        self.Attendance.date <= end_date
                    )
                ).scalar()
                
                present_count = self.db.session.query(func.count(self.Attendance.id)).filter(
                    and_(
                        self.Attendance.guard_id == guard.id,
                        self.Attendance.date >= start_date,
                        self.Attendance.date <= end_date,
                        self.Attendance.status == 'present'
                    )
                ).scalar()
                
                total_possible_attendance += days_count
                actual_attendance += present_count
            
            attendance_rate = (actual_attendance / total_possible_attendance * 100) if total_possible_attendance > 0 else 0
            
            location_data.append({
                'location': location,
                'total_guards': len(guards),
                'attendance_rate': attendance_rate,
                'total_possible': total_possible_attendance,
                'actual_present': actual_attendance
            })
        
        location_data.sort(key=lambda x: x['attendance_rate'], reverse=True)
        return location_data

    def _generate_daily_pdf(self, data, report_date):
        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4)
        story = []
        
        # Title
        title = f"PANOS SECURITY SERVICES LTD<br/>Daily Attendance Report<br/>{report_date.strftime('%A, %B %d, %Y')}"
        story.append(Paragraph(title, self.styles['PANOSTitle']))
        story.append(Spacer(1, 20))
        
        # Summary Statistics
        stats = data['statistics']
        summary_data = [
            ['Metric', 'Count', 'Percentage'],
            ['Total Guards', stats['total_guards'], '100%'],
            ['Guards Marked', stats['marked_guards'], f"{(stats['marked_guards']/stats['total_guards']*100):.1f}%" if stats['total_guards'] > 0 else '0%'],
            ['Present', stats['present'], f"{stats['attendance_rate']:.1f}%" if stats['marked_guards'] > 0 else '0%'],
            ['Absent', stats['absent'], f"{(stats['absent']/stats['marked_guards']*100):.1f}%" if stats['marked_guards'] > 0 else '0%'],
            ['Off Duty', stats['off'], f"{(stats['off']/stats['marked_guards']*100):.1f}%" if stats['marked_guards'] > 0 else '0%'],
            ['On Leave', stats['leave'], f"{(stats['leave']/stats['marked_guards']*100):.1f}%" if stats['marked_guards'] > 0 else '0%'],
        ]
        
        summary_table = Table(summary_data)
        summary_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#008000')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 12),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.black)
        ]))
        
        story.append(summary_table)
        story.append(Spacer(1, 30))
        
        # Detailed Records by Company
        story.append(Paragraph("Detailed Attendance Records", self.styles['PANOSHeading']))
        
        # Group records by company
        company_records = {}
        for record in data['records']:
            company_name = record[3].name
            if company_name not in company_records:
                company_records[company_name] = []
            company_records[company_name].append(record)
        
        for company_name, records in company_records.items():
            story.append(Paragraph(f"{company_name} ({len(records)} guards)", self.styles['Heading3']))
            
            detail_data = [['Guard Name', 'Location', 'Shift', 'Status', 'Time Marked']]
            
            for record in records:
                attendance, guard, location, company = record
                detail_data.append([
                    guard.name,
                    location.name,
                    attendance.shift.title(),
                    attendance.status.title(),
                    attendance.timestamp.strftime('%H:%M') if attendance.timestamp else 'N/A'
                ])
            
            detail_table = Table(detail_data)
            detail_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#FFD700')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 10),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                ('GRID', (0, 0), (-1, -1), 1, colors.black)
            ]))
            
            story.append(detail_table)
            story.append(Spacer(1, 20))
        
        # Footer
        footer_text = f"Report generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}<br/>PANOS Security Services Ltd."
        story.append(Spacer(1, 30))
        story.append(Paragraph(footer_text, self.styles['Normal']))
        
        doc.build(story)
        buffer.seek(0)
        return buffer
    
    def _generate_daily_csv(self, data, report_date):
        buffer = BytesIO()
        writer = csv.writer(buffer)
        
        # Write headers
        writer.writerow([f'Daily Attendance Report - {report_date.strftime("%Y-%m-%d")}'])
        writer.writerow([])
        
        # Summary
        stats = data['statistics']
        writer.writerow(['Metric', 'Count', 'Percentage'])
        writer.writerow(['Total Guards', stats['total_guards'], '100%'])
        writer.writerow(['Guards Marked', stats['marked_guards'], f"{(stats['marked_guards']/stats['total_guards']*100):.1f}%" if stats['total_guards'] > 0 else '0%'])
        writer.writerow(['Present', stats['present'], f"{stats['attendance_rate']:.1f}%" if stats['marked_guards'] > 0 else '0%'])
        writer.writerow(['Absent', stats['absent'], f"{(stats['absent']/stats['marked_guards']*100):.1f}%" if stats['marked_guards'] > 0 else '0%'])
        writer.writerow(['Off Duty', stats['off'], f"{(stats['off']/stats['marked_guards']*100):.1f}%" if stats['marked_guards'] > 0 else '0%'])
        writer.writerow(['On Leave', stats['leave'], f"{(stats['leave']/stats['marked_guards']*100):.1f}%" if stats['marked_guards'] > 0 else '0%'])
        writer.writerow([])
        
        # Detailed Records
        writer.writerow(['Detailed Attendance Records'])
        writer.writerow(['Company', 'Location', 'Guard Name', 'Shift', 'Status', 'Time Marked'])
        
        for record in data['records']:
            attendance, guard, location, company = record
            writer.writerow([
                company.name,
                location.name,
                guard.name,
                attendance.shift.title(),
                attendance.status.title(),
                attendance.timestamp.strftime('%H:%M') if attendance.timestamp else 'N/A'
            ])

        buffer.seek(0)
        return buffer
    
    def _generate_weekly_pdf(self, data, start_date, end_date):
        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4)
        story = []

        # Title
        title = f"PANOS SECURITY SERVICES LTD<br/>Weekly Attendance Report<br/>{start_date.strftime('%B %d, %Y')} to {end_date.strftime('%B %d, %Y')}"
        story.append(Paragraph(title, self.styles['PANOSTitle']))
        
        # Add a section for each day
        for day_data in data:
            day_date = day_data['date']
            stats = day_data['statistics']
            story.append(Spacer(1, 20))
            story.append(Paragraph(f"Summary for {day_date.strftime('%A, %B %d, %Y')}", self.styles['PANOSHeading']))

            # Daily summary table
            summary_data = [
                ['Metric', 'Count'],
                ['Present', stats['present']],
                ['Absent', stats['absent']],
                ['Off Duty', stats['off']],
                ['On Leave', stats['leave']],
            ]
            
            summary_table = Table(summary_data)
            summary_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#008000')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
                ('GRID', (0, 0), (-1, -1), 1, colors.black)
            ]))
            story.append(summary_table)
            story.append(Spacer(1, 15))

            # Detailed records for the day
            if day_data['records']:
                story.append(Paragraph("Detailed Records", self.styles['Heading3']))
                detail_data = [['Guard Name', 'Location', 'Shift', 'Status', 'Time Marked']]
                
                for record in day_data['records']:
                    attendance, guard, location, company = record
                    detail_data.append([
                        guard.name,
                        location.name,
                        attendance.shift.title(),
                        attendance.status.title(),
                        attendance.timestamp.strftime('%H:%M') if attendance.timestamp else 'N/A'
                    ])
                
                detail_table = Table(detail_data)
                detail_table.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#FFD700')),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
                    ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                    ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                    ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                    ('GRID', (0, 0), (-1, -1), 1, colors.black)
                ]))
                story.append(detail_table)
                story.append(Spacer(1, 10))

        # Footer
        footer_text = f"Report generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}<br/>PANOS Security Services Ltd."
        story.append(Spacer(1, 30))
        story.append(Paragraph(footer_text, self.styles['Normal']))
        
        doc.build(story)
        buffer.seek(0)
        return buffer
    
    def _generate_weekly_csv(self, data, start_date, end_date):
        buffer = BytesIO()
        writer = csv.writer(buffer)
        
        writer.writerow([f'Weekly Attendance Report: {start_date} to {end_date}'])
        writer.writerow([])
        
        for day_data in data:
            stats = day_data['statistics']
            writer.writerow([f"Summary for {day_data['date'].strftime('%A, %B %d, %Y')}"])
            writer.writerow(['Metric', 'Count'])
            writer.writerow(['Present', stats['present']])
            writer.writerow(['Absent', stats['absent']])
            writer.writerow(['Off Duty', stats['off']])
            writer.writerow(['On Leave', stats['leave']])
            writer.writerow([])

            writer.writerow(['Detailed Records'])
            writer.writerow(['Guard Name', 'Location', 'Shift', 'Status', 'Time Marked'])
            for record in day_data['records']:
                attendance, guard, location, company = record
                writer.writerow([
                    guard.name,
                    location.name,
                    attendance.shift.title(),
                    attendance.status.title(),
                    attendance.timestamp.strftime('%H:%M') if attendance.timestamp else 'N/A'
                ])
            writer.writerow([])
            
        buffer.seek(0)
        return buffer
    
    def _generate_performance_pdf(self, data, start_date, end_date):
        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4)
        story = []

        # Title
        title = f"PANOS SECURITY SERVICES LTD<br/>Guard Performance Report<br/>{start_date.strftime('%B %d, %Y')} to {end_date.strftime('%B %d, %Y')}"
        story.append(Paragraph(title, self.styles['PANOSTitle']))
        
        # Performance table
        performance_data = [['Guard Name', 'Present Days', 'Total Days', 'Attendance Rate (%)']]
        for record in data:
            performance_data.append([
                record['guard'].name,
                record['present_days'],
                record['total_days'],
                f"{record['attendance_rate']:.1f}%"
            ])
        
        performance_table = Table(performance_data)
        performance_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#008000')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.black)
        ]))
        
        story.append(performance_table)
        
        # Footer
        footer_text = f"Report generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}<br/>PANOS Security Services Ltd."
        story.append(Spacer(1, 30))
        story.append(Paragraph(footer_text, self.styles['Normal']))
        
        doc.build(story)
        buffer.seek(0)
        return buffer
    
    def _generate_performance_csv(self, data, start_date, end_date):
        buffer = BytesIO()
        writer = csv.writer(buffer)
        
        writer.writerow([f'Guard Performance Report: {start_date} to {end_date}'])
        writer.writerow([])
        
        writer.writerow(['Guard Name', 'Present Days', 'Total Days', 'Attendance Rate (%)'])
        for record in data:
            writer.writerow([
                record['guard'].name,
                record['present_days'],
                record['total_days'],
                f"{record['attendance_rate']:.1f}"
            ])
            
        buffer.seek(0)
        return buffer

    def _generate_location_pdf(self, data, start_date, end_date):
        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4)
        story = []

        # Title
        title = f"PANOS SECURITY SERVICES LTD<br/>Location Analysis Report<br/>{start_date.strftime('%B %d, %Y')} to {end_date.strftime('%B %d, %Y')}"
        story.append(Paragraph(title, self.styles['PANOSTitle']))
        
        # Location analysis table
        location_data = [['Location Name', 'Total Guards', 'Actual Present', 'Total Possible', 'Attendance Rate (%)']]
        for record in data:
            location_data.append([
                record['location'].name,
                record['total_guards'],
                record['actual_present'],
                record['total_possible'],
                f"{record['attendance_rate']:.1f}%"
            ])
        
        location_table = Table(location_data)
        location_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#008000')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.black)
        ]))
        
        story.append(location_table)
        
        # Footer
        footer_text = f"Report generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}<br/>PANOS Security Services Ltd."
        story.append(Spacer(1, 30))
        story.append(Paragraph(footer_text, self.styles['Normal']))
        
        doc.build(story)
        buffer.seek(0)
        return buffer
            
    def _generate_location_csv(self, data, start_date, end_date):
        buffer = BytesIO()
        writer = csv.writer(buffer)
        
        writer.writerow([f'Location Analysis Report: {start_date} to {end_date}'])
        writer.writerow([])
        
        writer.writerow(['Location Name', 'Total Guards', 'Actual Present', 'Total Possible', 'Attendance Rate (%)'])
        for record in data:
            writer.writerow([
                record['location'].name,
                record['total_guards'],
                record['actual_present'],
                record['total_possible'],
                f"{record['attendance_rate']:.1f}"
            ])
            
        buffer.seek(0)
        return buffer
