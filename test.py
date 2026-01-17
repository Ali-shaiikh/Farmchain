import streamlit as st
import pandas as pd
import numpy as np
from sklearn.linear_model import LinearRegression
from sklearn.model_selection import train_test_split
import os

st.title("Equipment Rental Price Predictor")


@st.cache_data
def load_data():
    try:
        data = pd.read_csv(r"C:\Users\ASUS\Downloads\farmer blockhin\equipment_model.csv")
        return data
    except FileNotFoundError:
        st.error("Dataset 'equipment_eth_new.csv' not found. Please upload the file.")
        return None


data = load_data()

if data is not None:
    st.subheader("Dataset Preview")
    st.write(data.head())

    X = data[["rental_price_per_day"]]
    y_min = data["min_price"]
    y_max = data["max_price"]

    X_train, X_test, y_min_train, y_min_test = train_test_split(
        X, y_min, test_size=0.2, random_state=42
    )
    _, _, y_max_train, y_max_test = train_test_split(
        X, y_max, test_size=0.2, random_state=42
    )

    model_min = LinearRegression()
    model_max = LinearRegression()

    model_min.fit(X_train, y_min_train)
    model_max.fit(X_train, y_max_train)

    min_score = model_min.score(X_test, y_min_test)
    max_score = model_max.score(X_test, y_max_test)
    st.subheader("Model Performance")
    st.write(f"R² Score for Min Price Prediction: {min_score:.4f}")
    st.write(f"R² Score for Max Price Prediction: {max_score:.4f}")

    st.subheader("Predict Min and Max Rental Prices")

    equipment_types = data["equipment_type"].unique().tolist()
    selected_equipment = st.selectbox("Select Equipment Type", equipment_types)

    rental_price = st.number_input(
        "Enter Rental Price per Day (in ETH)",
        min_value=0.0001,
        max_value=0.0150,
        step=0.0001,
        value=0.0050,
    )

    if st.button("Predict"):
        input_data = np.array([[rental_price]])

        predicted_min_price = model_min.predict(input_data)[0]
        predicted_max_price = model_max.predict(input_data)[0]

        if predicted_min_price >= rental_price:
            predicted_min_price = rental_price * 0.8
        if predicted_max_price <= rental_price:
            predicted_max_price = rental_price * 1.2

        st.success("Prediction Results:")
        st.write(f"**Equipment Type**: {selected_equipment}")
        st.write(f"**Entered Rental Price per Day**: {rental_price:.6f} ETH")
        st.write(f"**Predicted Min Price**: {predicted_min_price:.6f} ETH")
        st.write(f"**Predicted Max Price**: {predicted_max_price:.6f} ETH")

        st.subheader("Price Relationship Visualization")
        chart_data = pd.DataFrame(
            {
                "Price Type": ["Min Price", "Rental Price", "Max Price"],
                "Price (ETH)": [predicted_min_price, rental_price, predicted_max_price],
            }
        )
        st.bar_chart(chart_data.set_index("Price Type"))

else:
    st.write(
        "Please ensure the dataset 'equipment_eth_new.csv' is in the same directory as this script."
    )

st.sidebar.subheader("Instructions")
st.sidebar.write(
    """
1. Ensure 'equipment_eth_new.csv' is in the same directory as this script.
2. Select an equipment type from the dropdown.
3. Enter a rental price per day in ETH.
4. Click 'Predict' to see the predicted min and max prices.
"""
)